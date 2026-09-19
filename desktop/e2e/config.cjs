// Shared settings for the e2e scripts.
const fs = require("fs");
const os = require("os");
const path = require("path");

const SHOTS = path.join(os.tmpdir(), "pagi-e2e-shots");
fs.mkdirSync(SHOTS, { recursive: true });

module.exports = {
  // where the frontend (and, through its proxy, the backend) is served
  BASE: process.env.PAGI_E2E_URL || "http://localhost:5173",
  USER: process.env.PAGI_E2E_USER || "admin",
  PASSWORD: process.env.PAGI_E2E_PASSWORD || "admin",
  DESKTOP: path.resolve(__dirname, ".."),
  // `require("electron")` resolves to the electron binary path
  ELECTRON_EXE: require("electron"),
  // screenshots taken by the UI checks (never of the real screen)
  SHOTS,
};
