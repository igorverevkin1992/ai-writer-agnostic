// Юнит-тесты чистых модулей панели (nextstep, edits, highlight, diff, api) без дополнительных зависимостей:
// esbuild (уже есть у vite) собирает tests/*.test.ts в временную папку, прогоняет встроенный `node --test`.
// Запуск: `npm test` в panel/ (Node 22); из pytest — tests/test_аудит_панель.py::test_панель_js_тесты.
import { build } from "esbuild";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const tests = readdirSync(here).filter((f) => f.endsWith(".test.ts")).map((f) => join(here, f));
const out = mkdtempSync(join(tmpdir(), "konveyer-panel-tests-"));
try {
  await build({ entryPoints: tests, bundle: true, platform: "node", format: "esm", outdir: out, target: "node22",
                logLevel: "error", external: ["node:*"] });
  const files = readdirSync(out).filter((f) => f.endsWith(".js")).map((f) => join(out, f));
  const r = spawnSync(process.execPath, ["--test", ...files], { stdio: "inherit" });
  process.exit(r.status ?? 1);
} finally {
  rmSync(out, { recursive: true, force: true });
}
