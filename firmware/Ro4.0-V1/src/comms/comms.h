#pragma once

#include <WiFi.h>
#include <WiFiManager.h>
#include <PubSubClient.h>

class Sensors;
class Control;
class Commands;

class Comms {
public:
    // sensors is stored for config delivery from mqttCallback.
    void begin(Commands &cmds, Sensors &s);
    void update(Sensors &s, Control &c, Commands &cmds);

private:
    bool sendSnapshot = false;

    void reconnect();
};