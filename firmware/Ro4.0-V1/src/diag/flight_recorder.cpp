#include "flight_recorder.h"
#include <PubSubClient.h>

// ── Helpers ───────────────────────────────────────────────────────────────────

uint16_t FlightRecorder::count() const {
    return _full ? FR_SIZE : _write_idx;
}

// Returns the buffer index of the record that is `steps_back` positions
// behind the most recent write (0 = newest, count()-1 = oldest).
static inline uint16_t idxAt(uint16_t write_idx, uint16_t steps_back) {
    return (write_idx - 1 - steps_back + FR_SIZE) % FR_SIZE;
}

int FlightRecorder::appendRecord(char* buf, int buf_size, int pos,
                                  const FlightRecord& r, bool comma) const {
    if (comma && pos < buf_size - 1) buf[pos++] = ',';
    int written = snprintf(buf + pos, buf_size - pos,
        "{\"ts\":%u,\"st\":%u,\"fr\":%u,\"r\":%u,\"fg\":%u,"
        "\"f1\":%.2f,\"f2\":%.2f,"
        "\"p1a\":%d,\"p2a\":%d,\"p1\":%.2f,\"p2\":%.2f,"
        "\"t1r\":%d,\"t2r\":%d,\"t1m\":%d,\"t2m\":%d,"
        "\"in\":%u,\"out\":%u,\"rs\":%d}",
        r.ts, r.state, r.fault_reason, r.retry_count, r.flags,
        r.flow1_lpm, r.flow2_lpm,
        (int)r.p1_adc, (int)r.p2_adc, r.p1_bar, r.p2_bar,
        (int)r.tds1_raw, (int)r.tds2_raw, (int)r.tds1_mv, (int)r.tds2_mv,
        r.inputs, r.outputs, (int)r.rssi);
    return (written > 0) ? pos + written : pos;
}

// ── Public API ────────────────────────────────────────────────────────────────

void FlightRecorder::record(const FlightRecord& r) {
    _buf[_write_idx] = r;
    _write_idx = (_write_idx + 1) % FR_SIZE;
    if (_write_idx == 0) _full = true;
}

void FlightRecorder::captureFault(uint8_t reason, uint32_t fault_ts) {
    _fault_event  = true;
    _fault_reason = reason;
    _fault_ts     = fault_ts;
    Serial.printf("[FR] Falla capturada: reason=%u ts=%u buf=%u registros\n",
                  reason, fault_ts, count());
}

bool FlightRecorder::hasPendingFaultEvent() const { return _fault_event; }

void FlightRecorder::publishFaultEvent(PubSubClient& mqtt,
                                        const String& device_id) {
    static char buf[4096];

    uint16_t n    = count();
    uint16_t take = (n >= FR_FAULT_RECORDS) ? FR_FAULT_RECORDS : n;

    int pos = snprintf(buf, sizeof(buf),
        "{\"device_id\":\"%s\",\"type\":\"fault_event\","
        "\"fault_ts\":%u,\"fault_reason\":%u,\"count\":%u,\"records\":[",
        device_id.c_str(), _fault_ts, _fault_reason, take);

    // Export oldest-first (chronological order within the window)
    for (uint16_t i = 0; i < take; i++) {
        uint16_t steps_back = take - 1 - i;
        pos = appendRecord(buf, sizeof(buf), pos,
                           _buf[idxAt(_write_idx, steps_back)], i > 0);
    }

    if (pos < (int)sizeof(buf) - 2)
        snprintf(buf + pos, sizeof(buf) - pos, "]}");

    String topic = "fyntek/" + device_id + "/diag/flightrec";
    if (mqtt.publish(topic.c_str(), buf)) {
        Serial.printf("[FR] Fault event publicado: %u registros\n", take);
    } else {
        Serial.println("[FR] ERROR: fault event publish falló (buffer MQTT?)");
    }
    _fault_event = false;
}

void FlightRecorder::startDump() {
    uint16_t n   = count();
    _dump_total  = (n == 0) ? 0
                            : (n + FR_DUMP_CHUNK_SIZE - 1) / FR_DUMP_CHUNK_SIZE;
    _dump_chunk  = 0;
    _dumping     = (_dump_total > 0);
    Serial.printf("[FR] Dump iniciado: %u registros, %u chunks\n", n, _dump_total);
}

bool FlightRecorder::isDumping() const { return _dumping; }

bool FlightRecorder::publishNextChunk(PubSubClient& mqtt,
                                       const String& device_id) {
    if (!_dumping) return false;

    static char buf[4096];

    uint16_t n          = count();
    uint16_t from       = _dump_chunk * FR_DUMP_CHUNK_SIZE;  // oldest-first offset
    uint16_t to         = from + FR_DUMP_CHUNK_SIZE;
    if (to > n) to      = n;
    uint16_t chunk_n    = to - from;

    int pos = snprintf(buf, sizeof(buf),
        "{\"device_id\":\"%s\",\"type\":\"dump\","
        "\"chunk\":%u,\"total\":%u,\"records\":[",
        device_id.c_str(), _dump_chunk + 1, _dump_total);

    for (uint16_t i = 0; i < chunk_n; i++) {
        // oldest-first: record at index (from + i) from the oldest
        uint16_t steps_back = n - 1 - (from + i);
        pos = appendRecord(buf, sizeof(buf), pos,
                           _buf[idxAt(_write_idx, steps_back)], i > 0);
    }

    if (pos < (int)sizeof(buf) - 2)
        snprintf(buf + pos, sizeof(buf) - pos, "]}");

    String topic = "fyntek/" + device_id + "/diag/flightrec";
    mqtt.publish(topic.c_str(), buf);

    Serial.printf("[FR] Chunk %u/%u publicado\n", _dump_chunk + 1, _dump_total);

    if (++_dump_chunk >= _dump_total) {
        _dumping = false;
        Serial.println("[FR] Dump completo");
        return false;
    }
    return true;
}
