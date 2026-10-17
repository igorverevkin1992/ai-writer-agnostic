import { describe, expect, it } from 'vitest';
import { formatEpisodes, parseEpisodes, progressLine } from './api.ts';

describe('episode picker', () => {
  it('reads numbers and ranges and drops what is outside the season', () => {
    expect(parseEpisodes('1-3, 7; 60 70', 60)).toEqual([1, 2, 3, 7, 60]);
    expect(parseEpisodes('5–3', 60)).toEqual([3, 4, 5]);
    expect(parseEpisodes('', 60)).toEqual([]);
    expect(() => parseEpisodes('первая', 60)).toThrow(/Непонятно, что такое «первая»/u);
  });

  it('writes episodes back as ranges', () => {
    expect(formatEpisodes([3, 1, 2, 7, 10, 11, 12])).toBe('1–3, 7, 10–12');
    expect(formatEpisodes([4, 5])).toBe('4, 5');
  });
});

describe('progress line', () => {
  it('speaks in episodes, blocks and dollars', () => {
    expect(progressLine({ unit: 'episode', done: 6, total: 60, current: 7, costUsd: 3.2 }, 'scripts')).toBe('серия 7 из 60, $3.20');
    expect(progressLine({ unit: 'block', done: 1, total: 6, current: 20, costUsd: 1 }, 'episode_cards')).toBe('блок карточек 2 из 6 (до 20-й серии), $1.00');
    expect(progressLine({ unit: 'call', done: 0, total: 1, costUsd: 0 }, 'bible')).toBe('Библия, $0.00');
  });
});
