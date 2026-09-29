/**
 * Contract tests for the SDK adapters against a local fake HTTP server.
 * No real API is called: the SDKs are pointed at 127.0.0.1.
 */
import { createServer, type IncomingMessage, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { AnthropicProvider } from './anthropic.ts';
import { OutputTruncatedError, ProviderUnavailableError, RefusalError } from './errors.ts';
import { GoogleProvider } from './google.ts';
import { OpenAiCompatibleProvider } from './openaiCompatible.ts';

interface Seen {
  method: string;
  url: string;
  headers: IncomingMessage['headers'];
  body: Record<string, unknown>;
}

let server: Server;
let baseUrl: string;
let seen: Seen[] = [];
let respond: (req: Seen) => { status?: number; type?: string; body: string } = () => ({ body: '{}' });

beforeAll(async () => {
  server = createServer((req, res) => {
    let data = '';
    req.on('data', (c) => (data += c));
    req.on('end', () => {
      const s: Seen = { method: req.method ?? '', url: req.url ?? '', headers: req.headers, body: data ? JSON.parse(data) : {} };
      seen.push(s);
      const r = respond(s);
      res.writeHead(r.status ?? 200, { 'content-type': r.type ?? 'application/json' });
      res.end(r.body);
    });
  });
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(() => new Promise<void>((ok) => server.close(() => ok())));

beforeEach(() => {
  seen = [];
});

const request = {
  cacheablePrefix: ['БАЗА ЗНАНИЙ', 'БИБЛИЯ'],
  system: 'Инструкция шага',
  messages: [{ role: 'user' as const, content: 'Сделай' }],
};

function sse(events: [string, unknown][]): string {
  return events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('');
}

function anthropicStream(text: string, stopReason = 'end_turn'): string {
  return sse([
    ['message_start', {
      type: 'message_start',
      message: {
        id: 'msg_1', type: 'message', role: 'assistant', model: 'served-model', content: [],
        stop_reason: null, stop_sequence: null,
        usage: { input_tokens: 10, output_tokens: 1, cache_read_input_tokens: 700, cache_creation_input_tokens: 50 },
      },
    }],
    ['content_block_start', { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } }],
    ['content_block_delta', { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text } }],
    ['content_block_stop', { type: 'content_block_stop', index: 0 }],
    ['message_delta', { type: 'message_delta', delta: { stop_reason: stopReason, stop_sequence: null }, usage: { output_tokens: 42 } }],
    ['message_stop', { type: 'message_stop' }],
  ]);
}

describe('AnthropicProvider', () => {
  const env = { ANTHROPIC_API_KEY: 'test-key', ANTHROPIC_BASE_URL: '' };
  beforeAll(() => {
    env.ANTHROPIC_BASE_URL = baseUrl;
  });

  it('caches the knowledge-base prefix and maps usage', async () => {
    respond = () => ({ type: 'text/event-stream', body: anthropicStream('{"ok":true}') });
    const p = new AnthropicProvider(env);
    const res = await p.complete(request, { model: 'm-1', maxOutput: 1000, refusalFallback: 'default' });

    expect(res).toEqual({
      text: '{"ok":true}',
      model: 'served-model',
      usage: { inputTokens: 10, outputTokens: 42, cacheReadTokens: 700, cacheWriteTokens: 50 },
    });
    const call = seen[0]!;
    expect(call.url).toMatch(/^\/v1\/messages/);
    expect(call.headers['x-api-key']).toBe('test-key');
    expect(call.headers['anthropic-beta']).toContain('server-side-fallback-2026-07-01');
    expect(call.body).toMatchObject({ model: 'm-1', max_tokens: 1000, stream: true, fallbacks: 'default' });
    expect(call.body.system).toEqual([
      { type: 'text', text: 'БАЗА ЗНАНИЙ' },
      { type: 'text', text: 'БИБЛИЯ', cache_control: { type: 'ephemeral' } },
      { type: 'text', text: 'Инструкция шага' },
    ]);
  });

  it('sends no fallback beta when the role does not ask for it', async () => {
    respond = () => ({ type: 'text/event-stream', body: anthropicStream('{}') });
    await new AnthropicProvider(env).complete(request, { model: 'm-1', maxOutput: 10 });
    expect(seen[0]!.headers['anthropic-beta']).toBeUndefined();
    expect(seen[0]!.body.fallbacks).toBeUndefined();
  });

  it('reports refusal and truncation', async () => {
    respond = () => ({ type: 'text/event-stream', body: anthropicStream('', 'refusal') });
    await expect(new AnthropicProvider(env).complete(request, { model: 'm', maxOutput: 10 })).rejects.toThrow(RefusalError);
    respond = () => ({ type: 'text/event-stream', body: anthropicStream('{"a":', 'max_tokens') });
    await expect(new AnthropicProvider(env).complete(request, { model: 'm', maxOutput: 10 })).rejects.toThrow(
      OutputTruncatedError,
    );
  });

  it('after a server-side fallback keeps only the serving model answer', async () => {
    respond = () => ({
      type: 'text/event-stream',
      body: sse([
        ['message_start', {
          type: 'message_start',
          message: {
            id: 'msg_2', type: 'message', role: 'assistant', model: 'm-1', content: [], stop_reason: null, stop_sequence: null,
            usage: { input_tokens: 10, output_tokens: 1 },
          },
        }],
        ['content_block_start', { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } }],
        ['content_block_delta', { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: '{"a":' } }],
        ['content_block_stop', { type: 'content_block_stop', index: 0 }],
        ['content_block_start', {
          type: 'content_block_start', index: 1,
          content_block: { type: 'fallback', from: { model: 'm-1' }, to: { model: 'm-2' }, trigger: { type: 'refusal' } },
        }],
        ['content_block_stop', { type: 'content_block_stop', index: 1 }],
        ['content_block_start', { type: 'content_block_start', index: 2, content_block: { type: 'text', text: '' } }],
        ['content_block_delta', { type: 'content_block_delta', index: 2, delta: { type: 'text_delta', text: '{"ok":true}' } }],
        ['content_block_stop', { type: 'content_block_stop', index: 2 }],
        ['message_delta', { type: 'message_delta', delta: { stop_reason: 'end_turn', stop_sequence: null }, usage: { output_tokens: 20 } }],
        ['message_stop', { type: 'message_stop' }],
      ]),
    });
    const res = await new AnthropicProvider(env).complete(request, { model: 'm-1', maxOutput: 100, refusalFallback: 'default' });
    expect(res).toMatchObject({ text: '{"ok":true}', model: 'm-2' });
  });

  it('a truncated answer carries its usage for the cost log', async () => {
    respond = () => ({ type: 'text/event-stream', body: anthropicStream('{"a":', 'model_context_window_exceeded') });
    const err = await new AnthropicProvider(env).complete(request, { model: 'm', maxOutput: 10 }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(OutputTruncatedError);
    expect((err as OutputTruncatedError).billed?.usage.outputTokens).toBe(42);
  });

  it('counts tokens with the counting endpoint', async () => {
    respond = () => ({ body: JSON.stringify({ input_tokens: 1234 }) });
    expect(await new AnthropicProvider(env).countTokens(request, 'm-1')).toBe(1234);
    expect(seen[0]!.url).toMatch(/^\/v1\/messages\/count_tokens/);
  });

  it('treats server errors as unavailability (reserve can take over)', async () => {
    respond = () => ({ status: 401, body: JSON.stringify({ type: 'error', error: { type: 'authentication_error', message: 'bad key' } }) });
    await expect(new AnthropicProvider(env).countTokens(request, 'm')).rejects.toThrow(ProviderUnavailableError);
  });
});

describe('GoogleProvider', () => {
  const env = { GEMINI_API_KEY: 'g-key', GEMINI_BASE_URL: '' };
  beforeAll(() => {
    env.GEMINI_BASE_URL = baseUrl;
  });

  it('asks for JSON, maps usage including thinking tokens', async () => {
    respond = () => ({
      body: JSON.stringify({
        candidates: [{ content: { role: 'model', parts: [{ text: '{"ok":true}' }] }, finishReason: 'STOP' }],
        usageMetadata: { promptTokenCount: 1000, cachedContentTokenCount: 400, candidatesTokenCount: 30, thoughtsTokenCount: 70 },
        modelVersion: 'g-served',
      }),
    });
    const res = await new GoogleProvider(env).complete(request, { model: 'g-1', maxOutput: 500 });
    expect(res).toEqual({
      text: '{"ok":true}',
      model: 'g-served',
      usage: { inputTokens: 600, outputTokens: 100, cacheReadTokens: 400, cacheWriteTokens: 0 },
    });
    const call = seen[0]!;
    expect(call.url).toContain('g-1:generateContent');
    expect(call.body).toMatchObject({
      generationConfig: { maxOutputTokens: 500, responseMimeType: 'application/json' },
      systemInstruction: { parts: [{ text: 'БАЗА ЗНАНИЙ\n\nБИБЛИЯ\n\nИнструкция шага' }] },
      contents: [{ role: 'user', parts: [{ text: 'Сделай' }] }],
    });
  });

  it('reports safety blocks and truncation', async () => {
    respond = () => ({ body: JSON.stringify({ candidates: [{ content: { parts: [] }, finishReason: 'SAFETY' }] }) });
    await expect(new GoogleProvider(env).complete(request, { model: 'g', maxOutput: 5 })).rejects.toThrow(RefusalError);
    respond = () => ({ body: JSON.stringify({ candidates: [{ content: { parts: [{ text: '{' }] }, finishReason: 'MAX_TOKENS' }] }) });
    await expect(new GoogleProvider(env).complete(request, { model: 'g', maxOutput: 5 })).rejects.toThrow(
      OutputTruncatedError,
    );
  });

  it('counts tokens before a writer call', async () => {
    respond = () => ({ body: JSON.stringify({ totalTokens: 180000 }) });
    expect(await new GoogleProvider(env).countTokens(request, 'g-1')).toBe(180000);
    expect(seen[0]!.url).toContain('g-1:countTokens');
  });
});

describe('OpenAiCompatibleProvider (reserve)', () => {
  it('sends a chat completion and maps usage', async () => {
    respond = () => ({
      body: JSON.stringify({
        id: 'c1', object: 'chat.completion', created: 0, model: 'r-served',
        choices: [{ index: 0, message: { role: 'assistant', content: '{"ok":true}' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 100, completion_tokens: 20, total_tokens: 120, prompt_tokens_details: { cached_tokens: 40 } },
      }),
    });
    const env = { RESERVE_API_KEY: 'r-key', RESERVE_BASE_URL: `${baseUrl}/v1` };
    const res = await new OpenAiCompatibleProvider(env).complete(request, { model: 'r-1', maxOutput: 50 });
    expect(res.usage).toEqual({ inputTokens: 60, outputTokens: 20, cacheReadTokens: 40, cacheWriteTokens: 0 });
    expect(seen[0]!.url).toBe('/v1/chat/completions');
    expect(seen[0]!.headers.authorization).toBe('Bearer r-key');
    expect(seen[0]!.body.messages).toEqual([
      { role: 'system', content: 'БАЗА ЗНАНИЙ\n\nБИБЛИЯ\n\nИнструкция шага' },
      { role: 'user', content: 'Сделай' },
    ]);
  });
});
