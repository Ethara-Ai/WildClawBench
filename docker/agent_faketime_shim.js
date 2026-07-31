// WildClawBench agent clock shim.
//
// Loaded into the OpenClaw agent process via NODE_OPTIONS="--require <this>".
// OpenClaw builds its per-turn wake-up stamp ("[Www YYYY-MM-DD HH:MM UTC] ...")
// from `new Date()` on the container's real host clock. For persona tasks whose
// simulated window is months away from the real run date, that stamp (and any
// Date the agent's own tools read) contradicts the persona timeline the prompt
// asserts. This shim shifts wall-clock Date reads to the simulated persona time.
//
// Design constraints:
//   * Only `Date` is shifted. Node's timers (setTimeout/setInterval) and request
//     timeouts use libuv's MONOTONIC clock, not Date, so retry/timeout budgets
//     stay in real seconds — a far-future shift does not stall or fast-forward
//     the run.
//   * A single fixed offset is applied, so simulated time still ADVANCES in real
//     time from the base instant (not frozen).
//   * A Proxy over the real Date preserves `instanceof Date`, the prototype
//     chain, and the static surface (Date.parse / Date.UTC / etc.), so only
//     the "what time is it now" reads change.
//
// Opt-in: no-op unless WCB_FAKE_CLOCK_EPOCH_MS is set to a finite epoch (ms).
// Kill switch: WCB_DISABLE_AGENT_CLOCK_SIM=1 forces the no-op path.
(function installAgentClockShim() {
  if (process.env.WCB_DISABLE_AGENT_CLOCK_SIM === "1") return;

  const raw = process.env.WCB_FAKE_CLOCK_EPOCH_MS;
  if (!raw) return;

  const target = Number(raw);
  if (!Number.isFinite(target)) return;

  const RealDate = Date;
  const offset = target - RealDate.now();
  if (offset === 0) return;

  const shiftedNow = () => RealDate.now() + offset;

  const FakeDate = new Proxy(RealDate, {
    // `new Date()` with no args -> simulated now; any explicit args pass through.
    construct(Target, args) {
      if (args.length === 0) return new Target(shiftedNow());
      return new Target(...args);
    },
    // `Date()` called as a plain function returns a string of "now".
    apply() {
      return new RealDate(shiftedNow()).toString();
    },
    get(Target, prop, receiver) {
      if (prop === "now") return shiftedNow;
      return Reflect.get(Target, prop, receiver);
    },
  });

  globalThis.Date = FakeDate;

  if (process.env.WCB_FAKE_CLOCK_DEBUG === "1") {
    // eslint-disable-next-line no-console
    console.error(
      `[wcb-clock-shim] active: offset=${offset}ms simulated_now=${new FakeDate().toISOString()}`
    );
  }
})();
