import { z } from 'zod';
import { NonEmpty, maxWords } from './common.ts';

export const Logline = z.object({
  heroine: NonEmpty,
  wound: NonEmpty,
  betrayer: NonEmpty,
  motive: NonEmpty,
  revenge_goal: NonEmpty,
  twist_secret: NonEmpty,
  stakes: NonEmpty,
  text_35w: maxWords(35),
  ad_15w: maxWords(15),
});
export type Logline = z.infer<typeof Logline>;
