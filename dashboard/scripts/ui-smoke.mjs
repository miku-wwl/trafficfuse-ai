// Browser-driven UI smoke test over the Chrome DevTools Protocol (no extra deps).
//
//   node dashboard/scripts/ui-smoke.mjs --url http://127.0.0.1:8001/ \
//        --capability .runtime/trafficfuse/simulation/aig/capability --out ui-smoke.png
//
// Drives the real dashboard against a running backend (use SIMULATION mode for
// routine runs): probe -> AI diagnosis -> approve exact Plan -> authorize with the
// capability -> execute -> wait for the measured recovery verdict -> red-team
// refusal. The capability is read from the given file and typed into the
// password field; it is never printed. Requires Chrome or Edge.

import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((pairs, value, index, all) => (index % 2 === 0 ? [...pairs, [value.replace(/^--/, ""), all[index + 1]]] : pairs), []),
);
const url = args.url ?? "http://127.0.0.1:8001/";
const token = readFileSync(args.capability, "ascii").trim();
const out = args.out ?? "ui-smoke.png";
const browsers = [
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
];
const browser = args.browser ?? browsers.find((path) => existsSync(path));
const port = 9400 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), "tf-ui-smoke-"));
const chrome = spawn(browser, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-dark-mode", `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, "--window-size=1480,3600", "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let socket;
let nextId = 0;
const pending = new Map();
function send(method, params = {}) {
  const id = ++nextId;
  socket.send(JSON.stringify({ id, method, params }));
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}
async function evaluate(expression) {
  const reply = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (reply.exceptionDetails) throw new Error(reply.exceptionDetails.exception?.description ?? reply.exceptionDetails.text);
  return reply.result?.value;
}
async function waitFor(description, expression, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if (await evaluate(`Boolean(${expression})`)) return;
    } catch {
      /* page still loading or re-rendering: keep waiting */
    }
    await sleep(400);
  }
  throw new Error(`timed out waiting for: ${description}`);
}
const hasText = (text) => `document.body.innerText.includes(${JSON.stringify(text)})`;
const button = (text) =>
  `[...document.querySelectorAll("button")].find((b) => b.textContent.trim().includes(${JSON.stringify(text)}) && !b.disabled)`;
const click = (text) => evaluate(`(() => { const b = ${button(text)}; if (!b) throw new Error("no enabled button: ${text}"); b.click(); return true; })()`);
const fill = (selector, value) =>
  evaluate(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, ${JSON.stringify(value)});
    el.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  })()`);

const steps = [];
const log = (step) => {
  steps.push(step);
  console.log(`ok  ${step}`);
};

try {
  let target;
  for (let attempt = 0; attempt < 50 && !target; attempt++) {
    try {
      target = await (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, { method: "PUT" })).json();
    } catch {
      await sleep(200);
    }
  }
  socket = new WebSocket(target.webSocketDebuggerUrl);
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) reject(new Error(`CDP ${message.error.message}`));
      else resolve(message.result);
    }
  };
  await new Promise((resolve) => (socket.onopen = resolve));
  await send("Runtime.enable");

  await waitFor("dashboard loaded", button("Probe live Service now"));
  log("dashboard loaded with source badge: " + (await evaluate(`document.querySelector(".badge")?.textContent`)));
  await click("Probe live Service now");
  await waitFor("incident detected", button("Diagnose with AI"), 60000);
  log("incident detected from real probe evidence");
  await click("Diagnose with AI");
  // The approval buttons stay disabled until a reviewer name and the
  // fingerprint acknowledgement are entered, so wait for the gate's input.
  await waitFor("plan awaiting approval", `document.querySelector('input[placeholder="e.g. Incident commander"]')`, 240000);
  log("AI diagnosis grounded and Plan validated by the gate (awaiting approval)");
  await fill('input[placeholder="e.g. Incident commander"]', "UI smoke reviewer");
  await evaluate(`(() => { document.querySelector('.gate input[type="checkbox"]').click(); return true; })()`);
  await click("Approve exact Plan");
  await waitFor("approved", hasText("nothing was dispatched by approving"));
  log("exact Plan approved; nothing dispatched");
  await fill('input[type="password"]', token);
  await click("Authorize one Action");
  await waitFor("authorized", hasText("Still nothing dispatched"));
  log("independent authorization recorded");
  await click("Execute governed switch");
  await waitFor("recovery proven", hasText("Recovery proven by measurement"), 90000);
  log("executed once; recovery proven by independent re-measurement");
  await click("Send live traffic to the faulty version");
  await waitFor("red-team refusal", hasText("Refused by AIG"), 30000);
  log("unsafe proposal refused by Agentic Intent Gate");
  const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  writeFileSync(out, Buffer.from(shot.data, "base64"));
  log(`screenshot written to ${out}`);
  console.log(`UI_SMOKE=PASS (${steps.length} steps)`);
} catch (error) {
  console.error(`UI_SMOKE=FAIL after ${steps.length} steps: ${error.message}`);
  process.exitCode = 1;
} finally {
  socket?.close();
  chrome.kill();
}
