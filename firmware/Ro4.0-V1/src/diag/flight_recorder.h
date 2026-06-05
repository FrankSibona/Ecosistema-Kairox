#pragma once
#include <Arduino.h>

class PubSubClient;  // forward declaration — full header only in .cpp

// ── FlightRecord ──────────────────────────────────────────────────────────────
// One second of system state. 40 bytes packed.
// 300 records × 40 bytes = 12,000 bytes (12 KB) of circular RAM buffer.
//
// Bitmask conventions:
//   inputs  — bit0=D1 bit1=D2 bit2=D3 bit3=D4 bit4=D5 bit5=D6
//   outputs — bit0=pump_low bit1=pump_high bit2=valve_flush
//             bit3=valve_inlet bit4=pump_inlet bit5=pump_dose
//   flags   — bit0=flowFaultArmed bit1=recoveryFaultArmed
struct FlightRecord {
    uint32_t ts;           // unix timestamp (s)
    uint8_t  state;        // SystemState enum value
    uint8_t  fault_reason; // FaultReason enum value
    uint8_t  retry_count;
    uint8_t  flags;        // fault timer armed flags (see above)
    float    flow1_lpm;
    float    flow2_lpm;
    int16_t  p1_adc;       // raw analogRead (0–4095)
    int16_t  p2_adc;
    float    p1_bar;
    float    p2_bar;
    int16_t  tds1_raw;     // analogRead raw count
    int16_t  tds2_raw;
    int16_t  tds1_mv;      // analogReadMilliVolts
    int16_t  tds2_mv;
    uint8_t  inputs;       // digital input bitmask
    uint8_t  outputs;      // digital output bitmask
    int8_t   rssi;         // WiFi RSSI (dBm)
    uint8_t  _pad;         // align to 40 bytes
};  // sizeof = 40 bytes

constexpr uint16_t FR_SIZE            = 300;  // 300 s rolling window
constexpr uint8_t  FR_FAULT_RECORDS   = 20;   // pre-fault records in fault event
constexpr uint8_t  FR_DUMP_CHUNK_SIZE = 15;   // records per on-demand dump chunk

// ── FlightRecorder ────────────────────────────────────────────────────────────
// Circular buffer updated once per second regardless of diagnostic mode.
// On FAULT: captureFault() marks a pending event — comms publishes it next loop.
// On /diag/flightrec/get: startDump() begins a chunked export (one chunk/s).
//
// Fault event  → topic fyntek/{id}/diag/flightrec  type="fault_event"  last 20 records
// Dump chunk   → topic fyntek/{id}/diag/flightrec  type="dump"  chunk/total
class FlightRecorder {
public:
    void record(const FlightRecord& r);

    void captureFault(uint8_t reason, uint32_t fault_ts);
    bool hasPendingFaultEvent() const;
    void publishFaultEvent(PubSubClient& mqtt, const String& device_id);

    void startDump();
    bool isDumping() const;
    // Returns true while more chunks remain, false when dump is complete.
    bool publishNextChunk(PubSubClient& mqtt, const String& device_id);

private:
    FlightRecord _buf[FR_SIZE];
    uint16_t     _write_idx    = 0;
    bool         _full         = false;

    bool         _fault_event  = false;
    uint32_t     _fault_ts     = 0;
    uint8_t      _fault_reason = 0;

    bool         _dumping      = false;
    uint16_t     _dump_chunk   = 0;
    uint16_t     _dump_total   = 0;

    uint16_t count() const;
    // Appends one serialized record to buf[pos..]. Returns new pos.
    int appendRecord(char* buf, int buf_size, int pos,
                     const FlightRecord& r, bool comma) const;
};
