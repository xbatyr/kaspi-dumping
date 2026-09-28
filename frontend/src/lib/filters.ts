/** What the catalogue shows before the merchant picks a filter.
 *
 * «В наличии» rather than everything, as in AlgaTop: PCs and setups that are
 * out of stock or switched off are noise on the screen the merchant works in.
 * «Все товары» stays one click away in the filter.
 *
 * A plain module, not a client component, so the server page reads the value
 * itself instead of a client reference.
 */
export const DEFAULT_SALE_FILTER = "on";
