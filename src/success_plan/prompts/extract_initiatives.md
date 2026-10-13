Task: list {company}'s key business initiatives.

Sources: the 10-K Business section (Item 1) and MD&A (Item 7) for fiscal year {fiscal_year}, and the most
recent earnings releases (Form 8-K Exhibit 99.1).

An initiative is something management says the company is doing or prioritizing: a strategy, investment,
program, product direction, operating change, or a goal management says it is pursuing (for example,
expanding margins). Report what the company says it is doing, not how it performed.

Not initiatives:
- Legal disputes or litigation.
- Analyst ratings, price targets, or stock-price movements.
- Financing and capital return: share repurchases or buybacks, dividends, debt, credit facilities, and
  notes offerings. If the chunk states a business purpose the financing serves, the initiative is that
  purpose, described without the financing. If an item is mainly about financing or capital return,
  give it the theme financing_capital_return; code removes those items from the initiatives list.
- Results. An initiative states what the company is doing or intends to do. Reporting a result, or the
  cause of a result, is not an initiative (for example, "revenue grew 14%" or "the CEO attributed margin
  expansion to execution").
- Forecasts and guidance figures, such as a raised full-year outlook or a guidance range. If management
  states a goal it is pursuing, such as margin expansion, the goal can be an initiative; describe the
  goal without the guidance numbers.

Return at most 12 initiatives.
