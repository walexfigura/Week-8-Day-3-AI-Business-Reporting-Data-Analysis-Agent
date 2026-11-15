# Company Context — Northstar Supply (fictional)

> Northstar Supply is a **fictional** company used throughout Week 8 of
> *10 Weeks to AI Agents* (Day 1: customer support agent, Day 2: competitive
> intelligence agent, Day 3: this business analyst). Every name, number and
> customer in this repository is invented for teaching.

## Business model

Northstar Supply is a small direct-to-consumer (DTC) ecommerce brand that sells
insulated drinkware and everyday carry products through its own online store.
It buys finished goods from contract manufacturers, stores them at a
third-party logistics (3PL) warehouse, and ships each order to the customer.

Money flows like this:

1. A customer orders on the Shopify store and pays by card.
2. The payment processor keeps a fee (2.9% + $0.30 per charge).
3. The 3PL picks, packs and ships the order and invoices Northstar.
4. Meta (Facebook/Instagram) ads bring in a large share of new customers.

## Product categories

| Category | SKUs | Notes |
|---|---|---|
| Bottles | NS-BTL-20 Summit 20 oz, NS-BTL-32 Summit 32 oz | Core products, healthy margins |
| Drinkware | NS-TUM-30 Commuter Tumbler, NS-CUP-KID Kids Cup | Kids Cup is the lowest-margin item |
| Bags | NS-BAG-LUN Basecamp Lunch Bag | Mid margin |
| Bundles | NS-KIT-HIK Trailhead Hiking Gift Kit | High price, lower margin %, promoted for gifting |
| Accessories | NS-LID-STR Flip Straw Lid, NS-BRS-CLN Brush Set | Low price, high margin % |

Unit costs live in the finance team's Google Sheet. The Summit 32 oz bottle's
landed cost rose from $12.75 to $13.50 on 2026-11-02 (supplier steel surcharge).

## Sales channels

- **Online store (Shopify)** — the only sales channel modeled in this project.
- Wholesale and marketplaces are out of scope.

## Advertising channels

- **Meta Ads** (Facebook + Instagram) is the only paid channel modeled. Five
  campaigns appear in the data: evergreen prospecting, retargeting, a 1%
  lookalike audience, a seasonal **Holiday Gift Push** (launched 2026-10-30,
  scaled up from 2026-11-02) and a paused Kids Cup test.
- Email, organic social and search exist in real life but are not modeled.
  Their absence is one reason Meta-attributed numbers cannot be reconciled
  to Shopify sales.

## Operations

- The 3PL changed its rate card on 2026-11-02 (pick & pack $2.10 → $2.85 per
  order) and switched to gift-ready mailers ($0.45 → $0.55).
- The carrier applies a peak-season surcharge in November.

## Business priorities (Q4)

1. Grow holiday sales without letting contribution margin fall below target.
2. Keep Meta spend efficient (see `business-targets.md`).
3. Keep refund rates low and fix product-quality issues quickly.
4. Make weekly reporting trustworthy: every number traceable to data.

## People who read the reports

- **Founder/CEO** — wants the headline and decisions needed.
- **Head of Growth** — owns Meta budgets and campaigns.
- **Operations lead** — owns the 3PL relationship and fulfillment costs.
- **Finance** — owns the cost sheet and targets; reviews every report before it is shared.
