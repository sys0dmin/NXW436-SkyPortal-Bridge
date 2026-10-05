#include <unity.h>

#include <cstdint>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include "celestron_aux/AuxCodec.h"
#include "celestron_aux/AuxCoordinateAdapter.h"
#include "celestron_aux/AuxStreamParser.h"
#include "celestron_aux/SemanticCore.h"
#include "domain/PositionMath.h"
#include "mount/FakeMountBackend.h"
#include "mount/FakeMountRuntime.h"
#include "net/Hbg3Advertisement.h"
#include "net/HbgTimeout.h"
#include "net/TcpTiming.h"
#include "nxw436/Nxw436ReadOnlyBackend.h"

namespace domain = nxw436::domain;
namespace mount = nxw436::mount;
namespace net = nxw436::net;
namespace j1_uart = nxw436::j1;
using namespace nxw436::celestron_aux;

void setUp() {}
void tearDown() {}

static std::vector<std::string> split(const std::string& line, const char delimiter) {
    std::vector<std::string> parts;
    std::stringstream stream(line);
    std::string part;
    while (std::getline(stream, part, delimiter)) parts.push_back(part);
    return parts;
}

static std::vector<std::uint8_t> fromHex(const std::string& value) {
    std::vector<std::uint8_t> bytes;
    for (std::size_t i = 0; i < value.size(); i += 2) {
        bytes.push_back(static_cast<std::uint8_t>(std::stoul(value.substr(i, 2), nullptr, 16)));
    }
    return bytes;
}

static std::string toHex(const std::uint8_t* data, const std::size_t length) {
    static constexpr char kHex[] = "0123456789ABCDEF";
    std::string result;
    result.reserve(length * 2);
    for (std::size_t i = 0; i < length; ++i) {
        result.push_back(kHex[data[i] >> 4]);
        result.push_back(kHex[data[i] & 0x0F]);
    }
    return result;
}

static void test_position_math_python_golden() {
    std::ifstream file("test/fixtures/position_math.csv");
    TEST_ASSERT_TRUE(file.good());
    std::string line;
    std::getline(file, line);
    while (std::getline(file, line)) {
        const auto fields = split(line, ',');
        const auto a = std::stoll(fields[1]);
        const auto expected = std::stoll(fields[3]);
        if (fields[0] == "N") {
            TEST_ASSERT_EQUAL_INT64(expected, domain::normalizePosition(a));
        } else {
            const auto b = std::stoll(fields[2]);
            TEST_ASSERT_EQUAL_INT64(expected, domain::signedModularDelta(a, b));
        }
    }
}

static void test_coordinate_python_golden() {
    std::ifstream file("test/fixtures/coordinate.csv");
    TEST_ASSERT_TRUE(file.good());
    std::string line;
    std::getline(file, line);
    while (std::getline(file, line)) {
        const auto f = split(line, ',');
        const bool az = f[0] == "AZ";
        AxisCoordinateConfig cfg{
            static_cast<std::uint32_t>(std::stoul(f[2])),
            static_cast<std::uint32_t>(std::stoul(f[3])),
            static_cast<std::uint32_t>(std::stoul(f[4])),
            static_cast<std::int8_t>(std::stoi(f[1])), true, 0, 0,
        };
        AuxCoordinateAdapter adapter(cfg, cfg);
        const auto input = static_cast<std::uint32_t>(std::stoul(f[6]));
        const auto expected = static_cast<std::uint32_t>(std::stoul(f[7]));
        std::uint32_t actual{};
        const bool ok = f[5] == "TO"
            ? adapter.toAux(az, input, actual)
            : adapter.fromAux(az, input, actual);
        TEST_ASSERT_TRUE(ok);
        TEST_ASSERT_EQUAL_UINT32(expected, actual);
    }
}

static void test_aux_codec_roundtrip_and_checksum() {
    AuxFrame frame{};
    frame.source = 0x20; frame.destination = 0x10; frame.command = 0x01;
    AuxWireBuffer wire{}; AuxError error{};
    TEST_ASSERT_TRUE(serializeAux(frame, wire, error));
    TEST_ASSERT_EQUAL_STRING("3B03201001CC", toHex(wire.bytes.data(), wire.length).c_str());
    AuxFrame decoded{};
    TEST_ASSERT_TRUE(deserializeAux(wire.bytes.data(), wire.length, decoded, error));
    TEST_ASSERT_TRUE(frame == decoded);
}

static void test_stream_parser_fragment_coalesce_garbage_and_recovery() {
    const auto first = fromHex("3B03201001CC");
    const auto second = fromHex("3B03201101CB");
    AuxStreamParser parser;
    auto events = parser.feed(first.data(), 3);
    TEST_ASSERT_TRUE(events.empty());
    std::vector<std::uint8_t> tail(first.begin() + 3, first.end());
    tail.insert(tail.end(), second.begin(), second.end());
    events = parser.feed(tail.data(), tail.size());
    TEST_ASSERT_EQUAL_UINT32(2, events.size());
    TEST_ASSERT_TRUE(events[0].valid);
    TEST_ASSERT_TRUE(events[1].valid);

    parser.reset();
    std::vector<std::uint8_t> garbage{'x', 'y', 'z'};
    garbage.insert(garbage.end(), first.begin(), first.end());
    events = parser.feed(garbage.data(), garbage.size());
    TEST_ASSERT_EQUAL_UINT32(1, events.size());
    TEST_ASSERT_TRUE(events[0].valid);

    parser.reset();
    auto bad = first; bad.back() = 0;
    bad.insert(bad.end(), second.begin(), second.end());
    events = parser.feed(bad.data(), bad.size());
    TEST_ASSERT_EQUAL_UINT32(2, events.size());
    TEST_ASSERT_FALSE(events[0].valid);
    TEST_ASSERT_EQUAL(AuxError::ChecksumMismatch, events[0].error);
    TEST_ASSERT_TRUE(events[1].valid);

    parser.reset();
    const std::vector<std::uint8_t> malformed{0x3B, 0x02};
    events = parser.feed(malformed.data(), malformed.size());
    TEST_ASSERT_EQUAL_UINT32(1, events.size());
    TEST_ASSERT_EQUAL(AuxError::ImpossibleLength, events[0].error);
    events = parser.feed(first.data(), first.size());
    TEST_ASSERT_EQUAL_UINT32(1, events.size());
    TEST_ASSERT_TRUE(events[0].valid);
}

static void test_stream_parser_bounded_buffer() {
    AuxStreamParser parser(8);
    const std::vector<std::uint8_t> incomplete{
        0x3B, 0xFF, 0, 0, 0, 0, 0, 0, 0,
    };
    const auto events = parser.feed(incomplete.data(), incomplete.size());
    TEST_ASSERT_EQUAL_UINT32(1, events.size());
    TEST_ASSERT_FALSE(events[0].valid);
    TEST_ASSERT_EQUAL(AuxError::PayloadTooLarge, events[0].error);
    TEST_ASSERT_LESS_OR_EQUAL_UINT32(8, parser.buffered());
}

static void test_embedded_bounded_parser_api() {
    const auto first = fromHex("3B03201001CC");
    const auto second = fromHex("3B03201101CB");
    std::vector<std::uint8_t> input = first;
    input.insert(input.end(), second.begin(), second.end());
    AuxStreamParser parser;
    const auto batch = parser.feedBounded(input.data(), input.size());
    TEST_ASSERT_FALSE(batch.overflow);
    TEST_ASSERT_EQUAL_UINT32(2, batch.count);
    TEST_ASSERT_TRUE(batch.events[0].valid);
    TEST_ASSERT_TRUE(batch.events[1].valid);
}

static void replayFixture(const std::string& name) {
    mount::FakeMountBackend backend(16, 32);
    AxisCoordinateConfig config{domain::kPositionModulus, 0, 0, 1, true, 0, 0};
    const SemanticProfile profile{
        name == "startup_manual", name == "startup_manual",
    };
    SemanticCore core(backend, AuxCoordinateAdapter(config, config), profile);
    std::ifstream file("test/fixtures/" + name + ".fixture");
    TEST_ASSERT_TRUE(file.good());
    std::string line;
    while (std::getline(file, line)) {
        if (line.empty() || line[0] == '#') continue;
        const auto fields = split(line, ';');
        if (fields[0] == "COMPLETE") {
            const auto axis = fields[1] == "AZ" ? mount::Axis::Az : mount::Axis::Alt;
            backend.setPosition(axis, core.gotoTarget(axis));
            core.completeGoto(axis);
            continue;
        }
        const auto rx = fromHex(fields[1]);
        AuxFrame request{}; AuxError error{};
        TEST_ASSERT_TRUE(deserializeAux(rx.data(), rx.size(), request, error));
        const auto outcome = core.dispatch(request);
        if (fields[2] == "-") {
            TEST_ASSERT_FALSE(outcome.has_reply);
        } else {
            TEST_ASSERT_TRUE(outcome.has_reply);
            AuxWireBuffer tx{};
            TEST_ASSERT_TRUE(serializeAux(outcome.reply, tx, error));
            TEST_ASSERT_EQUAL_STRING(fields[2].c_str(), toHex(tx.bytes.data(), tx.length).c_str());
        }
    }
}

static void test_startup_manual_transcript_python_parity() {
    replayFixture("startup_manual");
}

static void test_goto_transcript_python_parity() {
    replayFixture("goto");
}

static void test_cancellation_transcript_python_parity() {
    replayFixture("cancel");
}

static void test_fake_backend_axis_isolation_and_wrap() {
    mount::FakeMountBackend backend(domain::kPositionModulus - 2, 10);
    backend.advance(mount::Axis::Az, 5);
    std::uint32_t az{}, alt{};
    TEST_ASSERT_TRUE(backend.getPosition(mount::Axis::Az, az));
    TEST_ASSERT_TRUE(backend.getPosition(mount::Axis::Alt, alt));
    TEST_ASSERT_EQUAL_UINT32(3, az);
    TEST_ASSERT_EQUAL_UINT32(10, alt);
}

static void test_unsupported_manual_rate_has_no_reply_or_motion() {
    mount::FakeMountBackend backend;
    AxisCoordinateConfig config{domain::kPositionModulus, 0, 0, 1, true, 0, 0};
    SemanticCore core(backend, AuxCoordinateAdapter(config, config));
    AuxFrame request{};
    request.source = 0x20;
    request.destination = kAzmAddress;
    request.command = 0x24;
    request.payload[0] = 0x0A;
    request.payload_length = 1;
    const auto outcome = core.dispatch(request);
    TEST_ASSERT_FALSE(outcome.has_reply);
    TEST_ASSERT_FALSE(backend.getAxisStatus(mount::Axis::Az).motion_commanded);
}

static void test_fake_runtime_exposes_active_position_then_completion() {
    mount::FakeMountBackend backend;
    AxisCoordinateConfig config{domain::kPositionModulus, 0, 0, 1, true, 0, 0};
    AuxCoordinateAdapter adapter(config, config);
    SemanticCore core(backend, adapter);
    mount::FakeMountRuntime runtime(backend, core, 6000);
    std::uint32_t target_aux{};
    TEST_ASSERT_TRUE(adapter.toAux(true, 6000, target_aux));
    AuxFrame request{};
    request.source = 0x20; request.destination = kAzmAddress; request.command = 0x02;
    request.payload[0] = target_aux >> 16;
    request.payload[1] = target_aux >> 8;
    request.payload[2] = target_aux;
    request.payload_length = 3;
    TEST_ASSERT_TRUE(core.dispatch(request).has_reply);
    runtime.tick(0);
    TEST_ASSERT_EQUAL(GotoState::Active, core.gotoState(mount::Axis::Az));
    runtime.tick(3000);
    std::uint32_t halfway{};
    TEST_ASSERT_TRUE(backend.getPosition(mount::Axis::Az, halfway));
    TEST_ASSERT_GREATER_THAN_UINT32(0, halfway);
    TEST_ASSERT_LESS_THAN_UINT32(6000, halfway);
    runtime.tick(6000);
    std::uint32_t final{};
    TEST_ASSERT_TRUE(backend.getPosition(mount::Axis::Az, final));
    TEST_ASSERT_EQUAL_UINT32(core.gotoTarget(mount::Axis::Az), final);
    TEST_ASSERT_EQUAL(GotoState::Completed, core.gotoState(mount::Axis::Az));
}

static void test_fake_runtime_cancellation_stops_without_completion() {
    mount::FakeMountBackend backend;
    AxisCoordinateConfig config{domain::kPositionModulus, 0, 0, 1, true, 0, 0};
    AuxCoordinateAdapter adapter(config, config);
    SemanticCore core(backend, adapter);
    mount::FakeMountRuntime runtime(backend, core, 6000);
    std::uint32_t target_aux{};
    TEST_ASSERT_TRUE(adapter.toAux(true, 6000, target_aux));
    AuxFrame request{};
    request.source = 0x20; request.destination = kAzmAddress; request.command = 0x02;
    request.payload[0] = target_aux >> 16;
    request.payload[1] = target_aux >> 8;
    request.payload[2] = target_aux;
    request.payload_length = 3;
    core.dispatch(request);
    runtime.tick(0);
    AuxFrame stop{};
    stop.source = 0x20; stop.destination = kAzmAddress; stop.command = 0x24;
    stop.payload[0] = 0; stop.payload_length = 1;
    TEST_ASSERT_TRUE(core.dispatch(stop).has_reply);
    runtime.tick(6000);
    TEST_ASSERT_EQUAL(GotoState::Cancelled, core.gotoState(mount::Axis::Az));
    std::uint32_t final{};
    backend.getPosition(mount::Axis::Az, final);
    TEST_ASSERT_NOT_EQUAL(core.gotoTarget(mount::Axis::Az), final);
}

static void test_hbg3_identity_exact_bytes() {
    const auto identity = net::buildHbg3Identity({0x00, 0x11, 0x22, 0x33, 0x44, 0x55});
    TEST_ASSERT_EQUAL_STRING("Celestron-334455", identity.ssid.data());
    TEST_ASSERT_EQUAL_STRING(
        "{\"mac\":\"00:11:22:33:44:55\",\n"
        "\"version\":\"HomeBrew-AMW007-9.0.0.0, 2021-10-18T12:00:00Z, ESP32-3.8\"\n}",
        identity.advertisement.data());
}

static void test_hbg_timeout_version_wire_is_source_backed() {
    AuxFrame probe{};
    probe.source = 0x20; probe.destination = 0xBD; probe.command = 0xFE;
    TEST_ASSERT_TRUE(net::isHbgTimeoutVersionProbe(probe));
    AuxWireBuffer request{}; AuxError error{};
    TEST_ASSERT_TRUE(serializeAux(probe, request, error));
    AuxWireBuffer timeout{};
    TEST_ASSERT_TRUE(net::buildHbgTimeoutWire(request, timeout));
    TEST_ASSERT_EQUAL_STRING("3B0320BDFE00", toHex(timeout.bytes.data(), timeout.length).c_str());
    probe.destination = kAzmAddress;
    TEST_ASSERT_FALSE(net::isHbgTimeoutVersionProbe(probe));
}

static void test_tcp_idle_timing_rejects_stale_pre_rx_now() {
    TEST_ASSERT_FALSE(net::elapsedAtLeast(100, 101, 15000));
    TEST_ASSERT_FALSE(net::elapsedAtLeast(15099, 100, 15000));
    TEST_ASSERT_TRUE(net::elapsedAtLeast(15100, 100, 15000));
    TEST_ASSERT_TRUE(net::elapsedAtLeast(5, 0xFFFFFF00U, 200));
}

static void test_azm_alt_only_profile_does_not_declare_accessories() {
    mount::FakeMountBackend backend;
    AxisCoordinateConfig config{domain::kPositionModulus, 0, 0, 1, true, 0, 0};
    SemanticCore core(backend, AuxCoordinateAdapter(config, config));
    AuxFrame b9{};
    b9.source = 0x20; b9.destination = 0xB9; b9.command = 0xFE;
    TEST_ASSERT_FALSE(core.dispatch(b9).has_reply);
    AuxFrame focus{};
    focus.source = 0x20; focus.destination = 0x12; focus.command = 0x2B;
    TEST_ASSERT_FALSE(core.dispatch(focus).has_reply);
    AuxFrame b5{};
    b5.source = 0x20; b5.destination = 0xB5; b5.command = 0x15;
    b5.payload_length = 10;
    TEST_ASSERT_FALSE(core.dispatch(b5).has_reply);
}

class ScriptedReadOnlyUart final : public j1_uart::INxw436ReadOnlyUart {
public:
    bool begin() override { return true; }
    j1_uart::PositionRead queryPosition(const bool az_axis) override {
        ++queries;
        last_az = az_axis;
        return next;
    }
    bool desynced() const override { return next.status == j1_uart::UartReadStatus::Desync; }
    void disableTx() override { ++disable_calls; }

    j1_uart::PositionRead next{};
    int queries{};
    int disable_calls{};
    bool last_az{};
};

static void test_read_only_backend_decodes_big_endian_and_rejects_motion() {
    ScriptedReadOnlyUart uart;
    uart.next.status = j1_uart::UartReadStatus::Ok;
    uart.next.query = 0x01;
    uart.next.raw[0] = 0x10; uart.next.raw[1] = 0x29; uart.next.raw[2] = 0xFF;
    j1_uart::Nxw436ReadOnlyBackend backend(uart);
    std::uint32_t position{};
    TEST_ASSERT_TRUE(backend.getPosition(mount::Axis::Az, position));
    TEST_ASSERT_EQUAL_UINT32(0x1029FF, position);
    TEST_ASSERT_TRUE(uart.last_az);
    TEST_ASSERT_FALSE(backend.move(mount::Axis::Az, mount::Direction::Plus, mount::SpeedTier::Fast));
    TEST_ASSERT_FALSE(backend.stop(mount::Axis::Az));
    TEST_ASSERT_FALSE(backend.getAxisStatus(mount::Axis::Az).motion_commanded);
}

static void test_read_only_backend_rejects_timeout_partial_and_out_of_range() {
    ScriptedReadOnlyUart uart;
    j1_uart::Nxw436ReadOnlyBackend backend(uart);
    std::uint32_t position{};
    uart.next.status = j1_uart::UartReadStatus::Timeout;
    TEST_ASSERT_FALSE(backend.getPosition(mount::Axis::Alt, position));
    uart.next.status = j1_uart::UartReadStatus::Partial;
    TEST_ASSERT_FALSE(backend.getPosition(mount::Axis::Alt, position));
    uart.next.status = j1_uart::UartReadStatus::Ok;
    uart.next.raw[0] = 0x10; uart.next.raw[1] = 0x2A; uart.next.raw[2] = 0x00;
    TEST_ASSERT_FALSE(backend.getPosition(mount::Axis::Alt, position));
}

int main(int, char**) {
    UNITY_BEGIN();
    RUN_TEST(test_position_math_python_golden);
    RUN_TEST(test_coordinate_python_golden);
    RUN_TEST(test_aux_codec_roundtrip_and_checksum);
    RUN_TEST(test_stream_parser_fragment_coalesce_garbage_and_recovery);
    RUN_TEST(test_stream_parser_bounded_buffer);
    RUN_TEST(test_embedded_bounded_parser_api);
    RUN_TEST(test_startup_manual_transcript_python_parity);
    RUN_TEST(test_goto_transcript_python_parity);
    RUN_TEST(test_cancellation_transcript_python_parity);
    RUN_TEST(test_fake_backend_axis_isolation_and_wrap);
    RUN_TEST(test_unsupported_manual_rate_has_no_reply_or_motion);
    RUN_TEST(test_fake_runtime_exposes_active_position_then_completion);
    RUN_TEST(test_fake_runtime_cancellation_stops_without_completion);
    RUN_TEST(test_hbg3_identity_exact_bytes);
    RUN_TEST(test_hbg_timeout_version_wire_is_source_backed);
    RUN_TEST(test_tcp_idle_timing_rejects_stale_pre_rx_now);
    RUN_TEST(test_azm_alt_only_profile_does_not_declare_accessories);
    RUN_TEST(test_read_only_backend_decodes_big_endian_and_rejects_motion);
    RUN_TEST(test_read_only_backend_rejects_timeout_partial_and_out_of_range);
    return UNITY_END();
}
