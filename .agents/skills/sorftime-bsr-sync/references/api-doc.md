---
tags:
  - Sorftime
  - API
  - sanitized
---

# Sorftime API Minimal Reference

This file is a sanitized project-local reference for the weekly automation.
Do not add real Account-SK values, request logs, response dumps, or customer
data here.

## Authentication

- Base URL: `https://standardapi.sorftime.com/api`
- Authentication header: `Authorization: BasicAuth ${SORFTIME_API_KEY}`
- Content type: `application/json;charset=UTF-8`
- The real `SORFTIME_API_KEY` must be provided through the runtime environment
  or a local `.env` file that is ignored by Git.

## Domain

The weekly report automation uses `domain=1` for the US marketplace.

## CategoryRequest

Used by `sorftime-bsr-sync` to fetch category Top 100 BSR data.

- Endpoint: `POST /CategoryRequest?domain=1`
- Required parameter: `NodeId`
- Optional parameters:
  - `QueryDate`: report date in `YYYY-MM-DD`
  - `QueryStart`: start date in `YYYY-MM-DD`
  - `QueryDays`: legacy relative query window
- Weekly workflow usage:
  - Fetch each configured category for the report date.
  - Delete existing Doris rows for the same category/date before writing.
  - Write normalized rows to `DORIS_DATABASE.DORIS_TABLE`.

## Response Fields Used by This Project

The sync and report scripts consume a normalized subset of product fields:

| Field | Purpose |
| --- | --- |
| `ASIN` | Product identifier |
| `Brand` | Brand name |
| `Title` | Product title |
| `Photo` | Main product image URL |
| `Price` | Price in marketplace minor units |
| `Ratings` | Star rating |
| `RatingsCount` | Rating count |
| `ListingSalesVolumeOfMonth` | Estimated rolling 30-day unit sales |
| `ListingSalesOfMonth` | Estimated rolling 30-day sales in minor units |
| `OnlineDays` | Days since listing launch |
| `BsrCategory` | Category ranking data |

Fields outside the automation contract should be treated as optional.

## Error Handling Expectations

- Non-zero API `Code` values must be logged without exposing credentials.
- Rate-limit or quota errors should fail the category and be reported in the
  workflow summary.
- Retried requests must remain idempotent because Doris writes are preceded by
  date/category cleanup.
