// No browser installation needed: execute the actual themeControl source with
// two mock webviews sharing one origin's persistent storage.
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");
const source = fs.readFileSync("draftcoach/static/common.js", "utf8");
const begin = source.indexOf("  function themeControl(");
const end = source.indexOf("  function fmtTimer(", begin);
assert(begin >= 0 && end > begin);
const body = source.slice(begin, end).trim();
const memory = new Map();
const windows = [];

function view(search = "") {
  const listeners = new Map();
  const classes = new Set();
  const button = {textContent: "", attributes: {},
                  setAttribute(k, v) { this.attributes[k] = v; }};
  const host = {
    body: {classList: {
      toggle(k, on) { if (on) classes.add(k); else classes.delete(k); },
      contains(k) { return classes.has(k); }
    }},
  };
  const target = {
    document: host,
    window: {addEventListener(k, fn) {listeners.set(k,fn);}},
    localStorage: {
      getItem(k) { return memory.get(k) || null; },
      setItem(k, v) {
        memory.set(k, v);
        for (const other of windows) {
          if (other !== target && other.listeners.has("storage"))
            other.listeners.get("storage")({key:k,newValue:v});
        }
      }
    },
    URLSearchParams, location:{search},
    listeners, button, classes
  };
  windows.push(target);
  vm.runInNewContext("("+body+")", target)(button, "fd-overlay-theme-v2");
  return {button, dark() {return classes.has("graphite");}, close() {
    windows.splice(windows.indexOf(target),1);
  }};
}
let overlay = view();
assert(!overlay.dark(), "New user should still start light");
overlay.button.onclick();
assert(overlay.dark(), "Click should apply dark");
assert.equal(memory.get("fd-theme-preference-v1"), "graphite", "Click should persist dark");
overlay.close();
overlay = view();
assert(overlay.dark(), "Dark must survive reopening the app");
const coach = view();
assert(coach.dark(), "The coach must read the overlay's saved dark setting");
coach.button.onclick();
assert(!coach.dark(), "Coach switch should apply light");
assert(!overlay.dark(), "Overlay should receive cross-window change");
assert.equal(memory.get("fd-theme-preference-v1"), "porcelain");
overlay.close(); coach.close();
assert(!view().dark(), "Light must also survive restart");
console.log("PASS: dark and light persist across restart, coach/panel share preference");
