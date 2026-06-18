#include <Arduino.h>
#include <Wire.h>

#include <OneWire.h>
#include <DallasTemperature.h>

#define DEBUG false
#define DEBUG_BAUDRATE 9600
#define DEBUG_INTERVAL 500
#define I2C_BUS Wire1
#define I2C_ADDRESS 0x42
#define I2C_REGSIZE 24
#define ANALOG_CHANNELS 1
#define CHIPID 0xBD4F
#define ONE_WIRE_BUS D3
#define LOOP_DELAY 1
#define LOOP1_DELAY 100

OneWire oneWire(ONE_WIRE_BUS);
DallasTemperature sensors(&oneWire);

struct SmoothedAnalog {
  int pin = NOPIN;
  float alpha = 1.0;
  uint16_t threshold = 1;
  float smoothedValue = 0.0;
  uint16_t finalLockedValue = 0;

  void begin() {
    const uint16_t initialReading = pin == NOPIN ? 0 : analogRead(pin);
    smoothedValue = initialReading;
    finalLockedValue = initialReading;
  }

  uint16_t read() {
    const uint16_t rawReading = pin == NOPIN ? 0 : analogRead(pin);
    smoothedValue = (alpha * rawReading) + ((1.0 - alpha) * smoothedValue);
    if (abs((int)smoothedValue - finalLockedValue) > threshold) {
      finalLockedValue = (int)smoothedValue;
    }
    return finalLockedValue;
  }
};

SmoothedAnalog analogs[] = {
  { A0, 0.2, 4 },
};
const uint8_t digitalOutPins[] = { D2, D6 };
const uint8_t digitalInPins[] = { D7, D8, D9, D10 };

const size_t numAnalogs = sizeof(analogs) / sizeof(analogs[0]);
const size_t numDigitalIns = sizeof(digitalInPins) / sizeof(digitalInPins[0]);
const size_t numDigitalOuts = sizeof(digitalOutPins) / sizeof(digitalOutPins[0]);

#pragma pack(push, 1)

struct I2CRegisters {
  uint16_t chipId;
  uint8_t reserved[12];
  // 0x0E - 0x0F: Digital Output (Read-write)
  uint16_t digitalOut;
  // 0x10 - 0x11: Digital Input (Read-only)
  uint16_t digitalIn;
  // 0x12 - 0x13: Analog
  uint16_t analog[ANALOG_CHANNELS];
  // 0x14 - 0x17: Tempurature
  float temperature;
};

#pragma pack(pop)

volatile I2CRegisters i2cData = { 0 };
volatile uint8_t registerPointer = 0;

void receiveEvent(int howMany) {
  if (howMany <= 0) {
    return;
  }
  // The first byte received is the Register Address (Pointer)
  const uint8_t ptr = I2C_BUS.read();
  // Safety check: Ensure pointer is within our map size
  if (ptr < I2C_REGSIZE) {
    registerPointer = ptr;
  }
  while (--howMany > 0) {
    const uint8_t incoming = I2C_BUS.read();
    // Only allow writing to the Digital Output zone (0x0E - 0x0F)
    if (registerPointer >= 0x0E && registerPointer < 0x10) {
      ((uint8_t*)&i2cData)[registerPointer] = incoming;
    }
    if (++registerPointer >= I2C_REGSIZE) {
      registerPointer = 0;
    }
  }
}

void requestEvent() {
  I2C_BUS.write(((uint8_t*)&i2cData)[registerPointer]);
  if (++registerPointer >= I2C_REGSIZE) {
    registerPointer = 0;
  }
}

void setup() {
  if (DEBUG) {
    Serial.begin(DEBUG_BAUDRATE);
  }
  for (auto& a : analogs) {
    if (a.pin != NOPIN) {
      pinMode(a.pin, INPUT);
    }
    a.begin();
  }
  for (auto& pin : digitalInPins) {
    if (pin != NOPIN) {
      pinMode(pin, INPUT_PULLUP);
    }
  }
  for (auto& pin : digitalOutPins) {
    if (pin != NOPIN) {
      pinMode(pin, OUTPUT);
    }
  }
  i2cData.chipId = CHIPID;
  i2cData.temperature = -127.0;
  if (ONE_WIRE_BUS != NOPIN) {
    sensors.begin();
    sensors.requestTemperatures();
    i2cData.temperature = sensors.getTempCByIndex(0);
  }
  I2C_BUS.begin(I2C_ADDRESS);
  I2C_BUS.onReceive(receiveEvent);
  I2C_BUS.onRequest(requestEvent);
}

void loop() {
  for (size_t i = 0; i < numAnalogs; i++) {
    const uint16_t val = analogs[i].read();
    noInterrupts();
    i2cData.analog[i] = val;
    interrupts();
  }
  uint16_t digitalInPacked = 0;
  for (size_t i = 0; i < numDigitalIns; i++) {
    if (digitalInPins[i] == NOPIN) {
      continue;
    }
    // Read pin (inverted logic if using INPUT_PULLUP, remove ! if not)
    if (!digitalRead(digitalInPins[i])) {
      digitalInPacked = bitSet(digitalInPacked, i);
    }
  }
  float temperature = -127.0;
  if (ONE_WIRE_BUS != NOPIN) {
    sensors.requestTemperatures();
    temperature = sensors.getTempCByIndex(0);
  }
  noInterrupts();
  i2cData.digitalIn = digitalInPacked;
  const uint16_t targetOutput = i2cData.digitalOut;
  if (temperature > -127.0) {
    i2cData.temperature = temperature;
  }
  interrupts();
  for (size_t i = 0; i < numDigitalOuts; i++) {
    if (digitalOutPins[i] == NOPIN) {
      continue;
    }
    // Check bit 'i'
    digitalWrite(digitalOutPins[i], bitRead(targetOutput, i));
  }
  debugPrint();
  delay(LOOP_DELAY);
}

void debugPrint() {
  static unsigned long lastPrint = 0;
  if (DEBUG && millis() - lastPrint > DEBUG_INTERVAL) {
    if (numAnalogs) {
      Serial.print("Reg[0x12] (A_0): ");
      Serial.println(i2cData.analog[0]);
    }
    Serial.print(i2cData.temperature);
    Serial.println(" °C");
    lastPrint = millis();
  }
}