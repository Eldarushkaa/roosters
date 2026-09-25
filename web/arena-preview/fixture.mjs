/** Explicit, immutable ART-REVIEW fixture. Not quotes, balances or rules for live play.
 * Values intentionally match the design example. No random outcomes or economy formulas.
 * Each stake has its own display-only quote; clicking Fight never changes this fixture.
 */
export const fixture = Object.freeze({
  guest: 'C4FF', balance: 55610, level: 3, power: 201, xp: 252, xpMax: 300,
  stakes: [1000, 2500, 5000, 10000],
  quotes: {1000: [1531, 2158], 2500: [3828, 5395], 5000: [7656, 10791], 10000: [15312, 21582]},
  onlineQuotes: {1000: 1800, 2500: 4500, 5000: 9000, 10000: 18000},
  result: {stake: 5000, payout: 0, net: -5000, xp: 12, chance: 56},
  wins: 15, losses: 16,
});
