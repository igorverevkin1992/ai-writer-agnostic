import type { ProviderName } from './config.ts';
import { SameFamilyError } from './errors.ts';

/** Model family of a provider. The reserve provider is its own family. */
export function familyOf(provider: ProviderName): string {
  return provider;
}

/**
 * The main rule of the project: a model never checks itself.
 * The critic must come from a different family than the author of the text.
 */
export function assertCrossFamily(author: ProviderName, critic: ProviderName): void {
  if (familyOf(author) === familyOf(critic)) {
    throw new SameFamilyError(
      `Проверка отменена: текст написан моделью семейства «${familyOf(author)}», ` +
        `и проверять его должна модель другого семейства, а не «${familyOf(critic)}».`,
    );
  }
}
