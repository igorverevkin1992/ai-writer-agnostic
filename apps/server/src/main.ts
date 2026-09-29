import { KbLoadError, loadKb } from '@aiw/kb';
import { buildApp } from './app.ts';

const port = Number(process.env.SERVER_PORT ?? 3001);

let kb;
try {
  kb = loadKb();
} catch (err) {
  console.error(err instanceof KbLoadError ? err.message : err);
  process.exit(1);
}

const app = buildApp({ kb });

try {
  await app.listen({ port, host: '127.0.0.1' });
  console.log(`Сервер запущен: http://127.0.0.1:${port}`);
} catch (err) {
  console.error(err);
  process.exit(1);
}
