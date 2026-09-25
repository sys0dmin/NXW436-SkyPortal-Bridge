from __future__ import annotations

import unittest

from aux.hbg3_infrastructure_experiment import HBG3InfrastructureExperiment, hbg3_v38_advertisement
from aux.messages import MC_GET_VER


class FakeServer:
    active_connection = False
    capture = None


class RecordingUDP:
    def __init__(self) -> None:
        self.calls: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, payload: bytes, destination: tuple[str, int]) -> None:
        self.calls.append((payload, destination))


class HBG3InfrastructureExperimentTests(unittest.TestCase):
    def test_advertisement_matches_hbg3_version_template(self) -> None:
        payload = hbg3_v38_advertisement("01:02:03:04:05:06")
        self.assertEqual(payload, (
            b'{"mac":"01:02:03:04:05:06",\n"version":"'
            b'HomeBrew-AMW007-9.0.0.0, 2021-10-18T12:00:00Z, ESP32-3.8"\n}'
        ))

    def test_advertises_only_without_active_tcp_client(self) -> None:
        server = FakeServer()
        experiment = HBG3InfrastructureExperiment(server, bind="127.0.0.1", broadcast="127.0.0.1", mac="00:00:00:00:00:00")
        udp = RecordingUDP()
        self.assertTrue(experiment.advertise_once(udp))
        self.assertEqual(udp.calls[0][1], ("127.0.0.1", 55555))
        server.active_connection = True
        self.assertFalse(experiment.advertise_once(udp))
        self.assertEqual(len(udp.calls), 1)

    def test_unmatched_get_ver_stops_for_review(self) -> None:
        experiment = HBG3InfrastructureExperiment(FakeServer(), bind="127.0.0.1", broadcast="127.0.0.1", mac="00:00:00:00:00:00")
        experiment.observe_record({"command": MC_GET_VER, "dispatch_result": "recognized_synthetic_profile_unconfigured"})
        self.assertTrue(experiment.unmatched_get_ver_observed)
        self.assertTrue(experiment.stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
