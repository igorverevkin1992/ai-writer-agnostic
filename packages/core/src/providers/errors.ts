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

export class RefusalError extends LlmError {
  override name = 'RefusalError';
}

export class OutputTruncatedError extends LlmError {
  override name = 'OutputTruncatedError';
  constructor(model: string, maxOutput: number) {
    super(`Ответ модели ${model} обрезан на лимите ${maxOutput} токенов`);
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
