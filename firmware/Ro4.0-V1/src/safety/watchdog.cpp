#include "watchdog.h"
#include <Arduino.h>
#include <esp_task_wdt.h>
#include <esp_system.h>
#include <config.h>

void watchdogInit() {
    esp_task_wdt_init(WATCHDOG_TIMEOUT_SEC, true);
    esp_task_wdt_add(NULL);
}

void watchdogReset() {
    esp_task_wdt_reset();
}

void logResetReason() {
    // esp_reset_reason() es la API de IDF (categorías agregadas). Se incluyen
    // entre paréntesis los nombres de bajo nivel (rst:0x.. del bootloader ROM)
    // que corresponden a cada categoría, para correlacionar con el banner de
    // arranque impreso por la ROM antes de que corra este código.
    esp_reset_reason_t reason = esp_reset_reason();
    const char* name;
    switch (reason) {
        case ESP_RST_POWERON:   name = "POWERON_RESET";                          break;
        case ESP_RST_EXT:       name = "EXT_RESET";                              break;
        case ESP_RST_SW:        name = "SW_RESET";                               break;
        case ESP_RST_PANIC:     name = "PANIC";                                  break;
        case ESP_RST_INT_WDT:   name = "INT_WDT (TG1WDT_SYS_RESET)";             break;
        case ESP_RST_TASK_WDT:  name = "ESP_RST_TASK_WDT";                       break;
        case ESP_RST_WDT:       name = "OTHER_WDT (TG0WDT_SYS_RESET/RTCWDT_RESET)"; break;
        case ESP_RST_DEEPSLEEP: name = "DEEPSLEEP_WAKE";                         break;
        case ESP_RST_BROWNOUT:  name = "BROWNOUT";                               break;
        case ESP_RST_SDIO:      name = "SDIO";                                   break;
        default:                name = "UNKNOWN";                                break;
    }
    Serial.print("[BOOT] Reset reason: ");
    Serial.println(name);
}
