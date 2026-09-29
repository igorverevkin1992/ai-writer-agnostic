import { z } from 'zod';
import { NonEmpty } from './common.ts';

export const Concept = z.object({
  id: NonEmpty,
  title: NonEmpty,
  premise: NonEmpty,
  hook: NonEmpty,
  genre_formula: NonEmpty,
  reference_cases: z.array(NonEmpty).default([]),
});
export type Concept = z.infer<typeof Concept>;

/** The concept step always offers exactly three concepts to choose from. */
export const ConceptSet = z.array(Concept).length(3);
export type ConceptSet = z.infer<typeof ConceptSet>;
