import asyncio
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch

from go2_safe_control.safety import Velocity
from go2_safe_control.session import RobotSession


def motion_response(name: str) -> dict[str, object]:
    return {
        "data": {
            "header": {"status": {"code": 0}},
            "data": f'{{"name":"{name}"}}',
        }
    }


def sport_response(code: int = 0, data: str = "") -> dict[str, object]:
    """构造与真机一致的 Sport RPC 响应，避免测试用空字典掩盖协议错误。"""

    return {
        "data": {
            "header": {"status": {"code": code}},
            "data": data,
        }
    }


class DelayedResponse:
    def __init__(self, delay: float, value: object) -> None:
        self.delay = delay
        self.value = value


class FakePubSub:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, object]]] = []
        self.no_reply_messages: list[tuple[str, dict[str, object]]] = []

    async def publish_request_new(
        self,
        topic: str,
        options: dict[str, object],
    ) -> object:
        self.requests.append((topic, options))
        response = self.responses.pop(0) if self.responses else sport_response()
        if isinstance(response, DelayedResponse):
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            loop.call_later(response.delay, future.set_result, response.value)
            return await future
        return response

    def publish_without_callback(
        self,
        topic: str,
        data: dict[str, object],
    ) -> None:
        self.no_reply_messages.append((topic, data))


class FakeConnection:
    def __init__(self, responses: list[object]) -> None:
        self.datachannel = type("DataChannel", (), {})()
        self.datachannel.pub_sub = FakePubSub(responses)


class RobotSessionSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = RobotSession(lambda _kind, _message: None)

    def tearDown(self) -> None:
        self.session.shutdown()

    def test_nonzero_velocity_is_blocked_until_walk_mode_is_ready(self) -> None:
        requested = Velocity(0.15, 0.0, 0.0)

        self.session.update_velocity(requested)

        self.assertEqual(self.session._desired, Velocity.zero())

    def test_ready_walk_mode_allows_velocity_to_reach_watchdog(self) -> None:
        requested = Velocity(0.15, 0.0, 0.0)
        with self.session._state_lock:
            self.session._walk_ready = True

        self.session.update_velocity(requested)

        self.assertEqual(self.session._desired, requested)


class RobotSessionProtocolTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self, responses: list[object]) -> tuple[RobotSession, FakePubSub]:
        session = object.__new__(RobotSession)
        session._state_lock = threading.Lock()
        session._desired = Velocity.zero()
        session._last_update = 0.0
        session._connected = True
        session._walk_ready = False
        session._safety_epoch = 0
        session._stop_failure_reported = False
        session._on_event = lambda _kind, _message: None
        session._conn = FakeConnection(responses)
        session._command_lock = asyncio.Lock()
        session._action_lock = asyncio.Lock()
        pub_sub = session._conn.datachannel.pub_sub
        return session, pub_sub

    async def test_move_is_sent_as_fire_and_forget_without_waiting_for_response(self) -> None:
        session, pub_sub = self.make_session([])

        await session._send_move(Velocity(0.15, 0.0, 0.0))

        self.assertEqual(pub_sub.requests, [])
        self.assertEqual(len(pub_sub.no_reply_messages), 1)
        topic, payload = pub_sub.no_reply_messages[0]
        self.assertEqual(topic, "rt/api/sport/request")
        self.assertEqual(payload["header"]["identity"]["api_id"], 1008)
        self.assertEqual(
            payload["header"]["policy"],
            {"priority": 0, "noreply": True},
        )
        self.assertEqual(payload["parameter"], '{"x": 0.15, "y": 0.0, "z": 0.0}')
        self.assertEqual(payload["binary"], [])

    async def test_control_loop_streams_forward_frames_without_rpc_responses(self) -> None:
        session, pub_sub = self.make_session([])
        requested = Velocity(0.15, 0.0, 0.0)
        with session._state_lock:
            session._walk_ready = True
            session._desired = requested
            session._last_update = time.monotonic()

        original_publish = pub_sub.publish_without_callback

        def stop_after_three_frames(topic: str, data: dict[str, object]) -> None:
            original_publish(topic, data)
            if len(pub_sub.no_reply_messages) == 3:
                with session._state_lock:
                    session._connected = False

        pub_sub.publish_without_callback = stop_after_three_frames

        await asyncio.wait_for(session._control_loop(), timeout=1.0)

        self.assertEqual(len(pub_sub.no_reply_messages), 3)
        self.assertEqual(pub_sub.requests, [])
        self.assertTrue(
            all(
                message[1]["parameter"] == '{"x": 0.15, "y": 0.0, "z": 0.0}'
                for message in pub_sub.no_reply_messages
            )
        )

    async def test_ap_control_loop_logs_move_transition_without_spamming(self) -> None:
        session, pub_sub = self.make_session([])
        session._connection_mode = "AP"
        events: list[tuple[str, str]] = []
        session._on_event = lambda kind, message: events.append((kind, message))
        with session._state_lock:
            session._walk_ready = True
            session._desired = Velocity(0.15, 0.0, 0.0)
            session._last_update = time.monotonic()

        original_publish = pub_sub.publish_without_callback

        def stop_after_three_frames(topic: str, data: dict[str, object]) -> None:
            original_publish(topic, data)
            if len(pub_sub.no_reply_messages) == 3:
                with session._state_lock:
                    session._connected = False

        pub_sub.publish_without_callback = stop_after_three_frames
        await asyncio.wait_for(session._control_loop(), timeout=1.0)

        move_logs = [
            message
            for kind, message in events
            if kind == "diagnostic" and "Move 开始/更新" in message
        ]
        self.assertEqual(len(move_logs), 1)
        self.assertIn("x=+0.15", move_logs[0])

    async def test_prepare_walk_queries_normal_mode_before_enabling_motion(self) -> None:
        session, pub_sub = self.make_session([{}, motion_response("normal")])

        await session._prepare_walk_mode()

        self.assertTrue(session.walk_ready)
        self.assertEqual(
            pub_sub.requests,
            [
                ("rt/api/sport/request", {"api_id": 1003}),
                ("rt/api/motion_switcher/request", {"api_id": 1001}),
            ],
        )

    async def test_prepare_walk_accepts_mcf_mode_without_switching(self) -> None:
        session, pub_sub = self.make_session([{}, motion_response("mcf")])

        await session._prepare_walk_mode()

        self.assertTrue(session.walk_ready)
        self.assertEqual(
            pub_sub.requests,
            [
                ("rt/api/sport/request", {"api_id": 1003}),
                ("rt/api/motion_switcher/request", {"api_id": 1001}),
            ],
        )

    async def test_ap_prepare_walk_logs_reported_and_ready_modes(self) -> None:
        session, _pub_sub = self.make_session([{}, motion_response("mcf")])
        session._connection_mode = "AP"
        events: list[tuple[str, str]] = []
        session._on_event = lambda kind, message: events.append((kind, message))

        await session._prepare_walk_mode()

        diagnostics = [message for kind, message in events if kind == "diagnostic"]
        self.assertTrue(any("机器人报告当前运动模式：mcf" in item for item in diagnostics))
        self.assertTrue(any("行走模式准备完成：mcf" in item for item in diagnostics))

    async def test_prepare_walk_switches_non_normal_mode_and_rechecks(self) -> None:
        session, pub_sub = self.make_session(
            [{}, motion_response("ai"), {}, motion_response("normal")]
        )

        with patch("go2_safe_control.session.asyncio.sleep", new=AsyncMock()):
            await session._prepare_walk_mode()

        self.assertTrue(session.walk_ready)
        self.assertEqual(
            pub_sub.requests[2],
            (
                "rt/api/motion_switcher/request",
                {"api_id": 1002, "parameter": {"name": "normal"}},
            ),
        )
        self.assertEqual(pub_sub.requests[3][1], {"api_id": 1001})

    async def test_stop_during_prepare_prevents_walk_readiness(self) -> None:
        session, _pub_sub = self.make_session([{}, motion_response("normal")])
        session._safety_epoch = 2

        await session._prepare_walk_mode(safety_epoch=1)

        self.assertFalse(session.walk_ready)

    async def test_stand_down_stops_then_sends_official_posture_api(self) -> None:
        session, pub_sub = self.make_session([{}, {}])
        session._walk_ready = True

        with patch("go2_safe_control.session.asyncio.sleep", new=AsyncMock()):
            await session._stand_down()

        self.assertFalse(session.walk_ready)
        self.assertEqual(
            pub_sub.requests,
            [
                ("rt/api/sport/request", {"api_id": 1003}),
                ("rt/api/sport/request", {"api_id": 1005}),
            ],
        )

    async def test_stand_up_stops_then_sends_official_posture_api(self) -> None:
        session, pub_sub = self.make_session([{}, {}])
        session._walk_ready = True
        events: list[tuple[str, str]] = []
        session._on_event = lambda kind, message: events.append((kind, message))

        with patch("go2_safe_control.session.asyncio.sleep", new=AsyncMock()):
            await session._stand_up()

        self.assertFalse(session.walk_ready)
        self.assertEqual(
            pub_sub.requests,
            [
                ("rt/api/sport/request", {"api_id": 1003}),
                ("rt/api/sport/request", {"api_id": 1004}),
            ],
        )
        self.assertEqual(events[-1][0], "walk_not_ready")
        self.assertIn("StandUp", events[-1][1])

    async def test_additional_sport_action_stops_before_action_request(self) -> None:
        session, pub_sub = self.make_session([sport_response(), sport_response()])
        session._walk_ready = True

        with patch("go2_safe_control.session.asyncio.sleep", new=AsyncMock()):
            await session._sport_action("hello")

        self.assertFalse(session.walk_ready)
        self.assertEqual(
            pub_sub.requests,
            [
                ("rt/api/sport/request", {"api_id": 1003}),
                ("rt/api/sport/request", {"api_id": 1016}),
            ],
        )

    async def test_balance_stand_first_stands_up_then_enters_balance_mode(self) -> None:
        session, pub_sub = self.make_session(
            [sport_response(), sport_response(), sport_response()]
        )

        with patch("go2_safe_control.session.asyncio.sleep", new=AsyncMock()):
            await session._sport_action("balance_stand")

        self.assertEqual(
            pub_sub.requests,
            [
                ("rt/api/sport/request", {"api_id": 1003}),
                ("rt/api/sport/request", {"api_id": 1004}),
                ("rt/api/sport/request", {"api_id": 1002}),
            ],
        )

    async def test_slow_high_level_action_uses_longer_action_timeout(self) -> None:
        session, _pub_sub = self.make_session(
            [sport_response(), DelayedResponse(0.03, sport_response())]
        )
        session.REQUEST_TIMEOUT = 0.01
        session.ACTION_REQUEST_TIMEOUT = 0.10

        with patch("go2_safe_control.session.asyncio.sleep", new=AsyncMock()):
            await session._sport_action("sit")

    async def test_heart_confirmation_timeout_does_not_abort_completed_workflow_action(self) -> None:
        """比心可能已在真机完成但没有及时返回 RPC 确认，不能因此终止后续流程。"""

        session, _pub_sub = self.make_session([])
        session._send_request = AsyncMock(
            side_effect=[sport_response(), asyncio.TimeoutError()]
        )
        events: list[tuple[str, str]] = []
        session._on_event = lambda kind, message: events.append((kind, message))

        with patch("go2_safe_control.session.asyncio.sleep", new=AsyncMock()):
            await session._sport_action("heart")

        self.assertNotIn("action_error", [kind for kind, _message in events])
        self.assertEqual(events[-1][0], "action_warning")
        self.assertIn("未返回确认", events[-1][1])

    async def test_rejected_action_is_not_reported_as_connection_error(self) -> None:
        session, _pub_sub = self.make_session(
            [sport_response(), sport_response(3203, "unknown api")]
        )
        events: list[tuple[str, str]] = []
        session._on_event = lambda kind, message: events.append((kind, message))

        with patch("go2_safe_control.session.asyncio.sleep", new=AsyncMock()):
            await session._sport_action("sit")

        self.assertEqual(events[-1][0], "action_error")
        self.assertIn("3203", events[-1][1])
        self.assertNotIn("error", [kind for kind, _message in events])


if __name__ == "__main__":
    unittest.main()
