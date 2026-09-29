import type { Usage } from './types.ts';

/** Base class for errors that end up in front of the producer. Messages are in Russian. */
export class LlmError extends Error {
  override name = 'LlmError';
}

/** Network failure, auth failure, 5xx, rate limit after SDK retries: the reserve provider may help. */
export class ProviderUnavailableError extends LlmError {
  override name = 'ProviderUnavailableError';
  constructor(
    readonly provider: string,
    override readonly cause: unknown,
  ) {
    super(`Поставщик ${provider} недоступен: ${cause instanceof Error ? cause.message : String(cause)}`);
  }
}

export class MissingKeyError extends LlmError {
  override name = 'MissingKeyError';
  constructor(readonly envVar: string) {
    super(`Нет ключа ${envVar} в файле .env`);
  }
}

/** What a failed call still cost: the tokens are billed even when the answer is unusable. */
export interface BilledCall {
  model: string;
  usage: Usage;
}

export class RefusalError extends LlmError {
  override name = 'RefusalError';
  constructor(
    message: string,
    readonly billed?: BilledCall,
  ) {
    super(message);
  }
}

export class OutputTruncatedError extends LlmError {
  override name = 'OutputTruncatedError';
  constructor(
    model: string,
    maxOutput: number,
    readonly billed?: BilledCall,
    why = `на лимите ${maxOutput} токенов`,
  ) {
    super(`Ответ модели ${model} обрезан ${why}`);
  }
}

export class InvalidOutputError extends LlmError {
  override name = 'InvalidOutputError';
}

export class SameFamilyError extends LlmError {
  override name = 'SameFamilyError';
}

export class InputTooLargeError extends LlmError {
  override name = 'InputTooLargeError';
}

export class BudgetExceededError extends LlmError {
  override name = 'BudgetExceededError';
  constructor(
    readonly spentUsd: number,
    readonly limitUsd: number,
  ) {
    super(
      `Бюджет проекта исчерпан: потрачено $${spentUsd.toFixed(2)} из $${limitUsd.toFixed(2)}. ` +
        'Работа остановлена. Нужно решение продюсера: поднять лимит или остановиться.',
    );
  }
}
