#include <Wire.h>

const int DIR_PIN = 7;
const int STOP_PIN = 8;

const byte MCP4725_ADDR = 0x60;

const float DAC_VREF = 5.0;
float maxRpm = 300.0;
float speedRpm = 0.0;
int speedMv = 0;

bool pumpRunning = false;
String direction = "FWD";

bool pulseActive = false;
unsigned long pulseStartMs = 0;
unsigned long pulseDurationMs = 0;

String inputBuffer = "";

void setup() {
  pinMode(DIR_PIN, OUTPUT);
  pinMode(STOP_PIN, OUTPUT);

  // Safe startup for the wiring that was physically verified:
  // STOP_PIN LOW = stop, STOP_PIN HIGH = run.
  digitalWrite(STOP_PIN, LOW);
  digitalWrite(DIR_PIN, HIGH);

  Serial.begin(115200);
  Wire.begin();

  setSpeedMillivolts(0);

  Serial.println("ID:PUMP_CTRL_V2");
  Serial.println("Pump controller ready");
}

void loop() {
  handleSerial();

  if (pulseActive && millis() - pulseStartMs >= pulseDurationMs) {
    stopPump();
    pulseActive = false;
    Serial.println("OK STOP TIMEOUT");
  }
}

void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();

    if (c == '\n' || c == '\r') {
      if (inputBuffer.length() > 0) {
        processCommand(inputBuffer);
        inputBuffer = "";
      }
    } else {
      inputBuffer += c;
    }
  }
}

void processCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "ID?") {
    Serial.println("ID:PUMP_CTRL_V2");
    return;
  }

  if (cmd == "STATUS") {
    printStatus();
    return;
  }

  if (cmd == "STOP") {
    stopPump();
    pulseActive = false;
    Serial.println("OK STOP");
    return;
  }

  if (cmd.startsWith("DIR,")) {
    String dir = cmd.substring(4);
    if (!setDirection(dir)) {
      Serial.println("ERR BAD_DIR");
      return;
    }
    Serial.print("OK DIR ");
    Serial.println(direction);
    return;
  }

  if (cmd.startsWith("RUN,")) {
    String dir = cmd.substring(4);
    if (!setDirection(dir)) {
      Serial.println("ERR BAD_DIR");
      return;
    }
    runPump();
    pulseActive = false;
    Serial.print("OK RUN ");
    Serial.println(direction);
    return;
  }

  if (cmd.startsWith("PULSE,")) {
    int comma1 = cmd.indexOf(',');
    int comma2 = cmd.indexOf(',', comma1 + 1);
    if (comma2 < 0) {
      Serial.println("ERR BAD_FORMAT");
      return;
    }

    String dir = cmd.substring(comma1 + 1, comma2);
    unsigned long duration = cmd.substring(comma2 + 1).toInt();

    if (!setDirection(dir)) {
      Serial.println("ERR BAD_DIR");
      return;
    }
    if (duration < 1) {
      Serial.println("ERR BAD_TIME");
      return;
    }

    runPump();
    pulseActive = true;
    pulseStartMs = millis();
    pulseDurationMs = duration;

    Serial.print("OK PULSE ");
    Serial.print(direction);
    Serial.print(" ");
    Serial.println(duration);
    return;
  }

  if (cmd.startsWith("SPEEDV,")) {
    int mv = cmd.substring(7).toInt();
    if (mv < 0 || mv > 5000) {
      Serial.println("ERR BAD_MV");
      return;
    }
    setSpeedMillivolts(mv);
    Serial.print("OK SPEEDV ");
    Serial.println(speedMv);
    return;
  }

  if (cmd.startsWith("MAXRPM,")) {
    float rpm = cmd.substring(7).toFloat();
    if (rpm <= 0.0) {
      Serial.println("ERR BAD_MAXRPM");
      return;
    }
    maxRpm = rpm;
    Serial.print("OK MAXRPM ");
    Serial.println(maxRpm, 2);
    return;
  }

  if (cmd.startsWith("SPEED,")) {
    float rpm = cmd.substring(6).toFloat();
    if (rpm < 0.0 || rpm > maxRpm) {
      Serial.println("ERR BAD_RPM");
      return;
    }

    speedRpm = rpm;
    int mv = (int)((rpm / maxRpm) * 5000.0 + 0.5);
    setSpeedMillivolts(mv);

    Serial.print("OK SPEED ");
    Serial.print(speedRpm, 2);
    Serial.print(" RPM ");
    Serial.print(speedMv);
    Serial.println(" MV");
    return;
  }

  Serial.println("ERR UNKNOWN");
}

void stopPump() {
  digitalWrite(STOP_PIN, LOW);
  pumpRunning = false;
}

void runPump() {
  digitalWrite(STOP_PIN, HIGH);
  pumpRunning = true;
}

bool setDirection(String dir) {
  dir.trim();
  dir.toUpperCase();

  if (dir == "FWD") {
    digitalWrite(DIR_PIN, HIGH);
    direction = "FWD";
    return true;
  }

  if (dir == "REV") {
    digitalWrite(DIR_PIN, LOW);
    direction = "REV";
    return true;
  }

  return false;
}

void setSpeedMillivolts(int mv) {
  speedMv = mv;
  speedRpm = (maxRpm * speedMv) / 5000.0;

  uint16_t value = (uint16_t)((speedMv / 5000.0) * 4095.0 + 0.5);
  setDAC(value);
}

void setDAC(uint16_t value) {
  value &= 0x0FFF;

  Wire.beginTransmission(MCP4725_ADDR);
  Wire.write(0x40);
  Wire.write(value >> 4);
  Wire.write((value & 0x0F) << 4);
  Wire.endTransmission();
}

void printStatus() {
  Serial.print("STATUS ");
  Serial.print(pumpRunning ? "RUNNING" : "STOPPED");
  Serial.print(" DIR=");
  Serial.print(direction);
  Serial.print(" SPEED_RPM=");
  Serial.print(speedRpm, 2);
  Serial.print(" SPEED_MV=");
  Serial.print(speedMv);
  Serial.print(" MAX_RPM=");
  Serial.print(maxRpm, 2);

  if (pulseActive) {
    unsigned long elapsed = millis() - pulseStartMs;
    unsigned long remaining = elapsed >= pulseDurationMs ? 0 : pulseDurationMs - elapsed;
    Serial.print(" PULSE_REMAINING_MS=");
    Serial.print(remaining);
  }

  Serial.println();
}
