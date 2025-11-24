#define NUM_LANES 3

const int startButtonPin = 2;
const int sirenPin = 3;
const int lanePins[NUM_LANES] = {4, 5};

const unsigned long debounceDelay = 50;   // ms
const unsigned long reviewDelay = 5000;   // referee delay
const unsigned long streamInterval = 10;  // ms (update live time every 0.01 s)

unsigned long startMillis = 0;
unsigned long finishMillis[NUM_LANES];
unsigned long decisionTime[NUM_LANES];
bool hasFinished[NUM_LANES];
bool resultPrinted[NUM_LANES];
int pressCount[NUM_LANES];
int finalStatus[NUM_LANES]; // 0 = none, 1 = OK, 2 = DQ

// Debounce
int lastRawState[NUM_LANES];
int stableState[NUM_LANES];
unsigned long lastChangeTime[NUM_LANES];

bool raceActive = false;

// Start debounce
int lastStartRaw = HIGH;
int stableStart = HIGH;
unsigned long lastStartChange = 0;

unsigned long lastStreamTime = 0;

// -------------------- Helper: Print formatted time --------------------
void printFormattedTime(unsigned long ms) {
  unsigned int minutes = (ms / 60000UL);
  unsigned int seconds = (ms / 1000UL) % 60;
  unsigned int hundredths = (ms % 1000UL) / 10; // 0–99

  Serial.print(minutes);
  Serial.print(":");
  if (seconds < 10) Serial.print("0");
  Serial.print(seconds);
  Serial.print(":");
  if (hundredths < 10) Serial.print("0");
  Serial.print(hundredths);
}

// -------------------- Setup --------------------
void setup() {
  Serial.begin(9600); // faster for smooth streaming

  pinMode(startButtonPin, INPUT_PULLUP);
  pinMode(sirenPin, OUTPUT);

  for (int i = 0; i < NUM_LANES; i++) {
    pinMode(lanePins[i], INPUT_PULLUP);
    lastRawState[i] = HIGH;
    stableState[i] = HIGH;
    lastChangeTime[i] = 0;
    pressCount[i] = 0;
    finalStatus[i] = 0;
    hasFinished[i] = false;
    resultPrinted[i] = false;
    finishMillis[i] = 0;
    decisionTime[i] = 0;
  }

  Serial.println("Ready Timer press button to start");
}

// -------------------- Main Loop --------------------
void loop() {
  unsigned long now = millis();

  // --- Handle start button ---
  int sr = digitalRead(startButtonPin);
  if (sr != lastStartRaw) {
    lastStartChange = now;
    lastStartRaw = sr;
  }
  if (now - lastStartChange > debounceDelay) {
    if (sr != stableStart) {
      if (stableStart == HIGH && sr == LOW) {
        startRace(); // always restart the race when button pressed
      }
      stableStart = sr;
    }
  }

  // Turn siren ON while start button held
  digitalWrite(sirenPin, (digitalRead(startButtonPin) == LOW) ? HIGH : LOW);

  // --- Lane buttons ---
  for (int i = 0; i < NUM_LANES; i++) {
    int r = digitalRead(lanePins[i]);
    if (r != lastRawState[i]) {
      lastChangeTime[i] = now;
      lastRawState[i] = r;
    }

    if (now - lastChangeTime[i] > debounceDelay) {
      if (r != stableState[i]) {
        if (stableState[i] == HIGH && r == LOW) {
          if (raceActive) handleLanePress(i);
        }
        stableState[i] = r;
      }
    }

    // Handle delayed referee decision
    if (raceActive && finalStatus[i] != 0 && !resultPrinted[i] && decisionTime[i] > 0) {
      if (now - decisionTime[i] >= reviewDelay) {
        printFinalResult(i);
        resultPrinted[i] = true;
      }
    }
  }

  // --- STREAM LIVE TIME ---
  if (raceActive && (now - lastStreamTime >= streamInterval)) {
    unsigned long elapsedMillis = now - startMillis;

    Serial.print("TIME:");
    printFormattedTime(elapsedMillis);
    Serial.println();

    lastStreamTime = now;
  }
}

// -------------------- Start Race --------------------
void startRace() {
  raceActive = true;
  startMillis = millis();
  lastStreamTime = startMillis;

  // Reset all lanes
  for (int i = 0; i < NUM_LANES; i++) {
    pressCount[i] = 0;
    finalStatus[i] = 0;
    hasFinished[i] = false;
    resultPrinted[i] = false;
    finishMillis[i] = 0;
    decisionTime[i] = 0;
  }

  Serial.println("Start Race");
}

// -------------------- Handle Lane Press --------------------
void handleLanePress(int lane) {
  if (pressCount[lane] < 1000) pressCount[lane]++;

  if (pressCount[lane] == 1) {
    if (!hasFinished[lane]) {
      hasFinished[lane] = true;
      finishMillis[lane] = millis() - startMillis;
      Serial.print(lane + 1);
      Serial.print("TIME");
//      printFormattedTime(finishMillis[lane]);
      Serial.println();
    }
  } else if (pressCount[lane] == 3) {
    finalStatus[lane] = 1; // OK
    decisionTime[lane] = millis();
  } else if (pressCount[lane] == 5) {
    finalStatus[lane] = 2; // DQ
  }
}

// -------------------- Print Final Results --------------------
void printFinalResult(int lane) {
  Serial.print(lane + 1);
  if (finalStatus[lane] == 2) {
    Serial.println("DISQUALIFIED");
  } else if (finalStatus[lane] == 1) {
    Serial.print("FINALTIME");
//    printFormattedTime(finishMillis[lane]);
    Serial.println();
  }
}
