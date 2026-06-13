#include "io_catalog.h"
#include <string.h>

static const char* INPUT_NAMES[(uint8_t)LogicalInput::COUNT] = {
    "demand",
    "raw_water_available",
    "feed_tank_high",
    "feed_tank_low",
    "permeate_tank_high",
    "permeate_tank_low",
    "final_tank_high",
    "final_tank_low",
    "pressure_ok",
    "softener_regenerating",
    "well_low_level",
    "dosing_ok",
};

static const char* OUTPUT_NAMES[(uint8_t)LogicalOutput::COUNT] = {
    "low_pressure_pump",
    "high_pressure_pump",
    "well_pump",
    "transfer_pump",
    "flush_valve",
    "inlet_valve",
    "dosing_pump",
};

const char* logicalInputName(LogicalInput sig) {
    uint8_t i = (uint8_t)sig;
    return (i < (uint8_t)LogicalInput::COUNT) ? INPUT_NAMES[i] : "";
}

const char* logicalOutputName(LogicalOutput sig) {
    uint8_t i = (uint8_t)sig;
    return (i < (uint8_t)LogicalOutput::COUNT) ? OUTPUT_NAMES[i] : "";
}

LogicalInput logicalInputFromName(const char* name) {
    for (uint8_t i = 0; i < (uint8_t)LogicalInput::COUNT; i++) {
        if (strcmp(name, INPUT_NAMES[i]) == 0) return (LogicalInput)i;
    }
    return LogicalInput::COUNT;
}

LogicalOutput logicalOutputFromName(const char* name) {
    for (uint8_t i = 0; i < (uint8_t)LogicalOutput::COUNT; i++) {
        if (strcmp(name, OUTPUT_NAMES[i]) == 0) return (LogicalOutput)i;
    }
    return LogicalOutput::COUNT;
}
