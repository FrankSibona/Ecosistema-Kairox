#pragma once

#include <WiFi.h>
#include <WiFiManager.h>
#include <PubSubClient.h>

class Sensors;
class Control;
class Commands;
class DiagMode;
class FlightRecorder;

class Comms {
public:
    void begin(Commands &cmds, Sensors &s, DiagMode &diag, FlightRecorder &fr);
    void update(Sensors &s, Control &c, Commands &cmds, DiagMode &diag, FlightRecorder &fr);

private:
    bool sendSnapshot = false;

    void reconnect();
};