-- A buzz event is a day far above the brand's own baseline: a z-score against the previous
-- N days, with the day itself excluded so a spike does not inflate its own baseline.
{% set baseline_days = var('buzz_baseline_days') %}

with daily as (
    select brand_id, brand_name, category, view_date, views
    from {{ ref('fct_brand_daily') }}
),

scored as (
    select
        *,
        avg(views) over baseline as baseline_avg,
        stddev_samp(views) over baseline as baseline_std,
        count(*) over baseline as baseline_rows
    from daily
    window baseline as (
        partition by brand_id order by view_date
        rows between {{ baseline_days }} preceding and 1 preceding
    )
)

select
    brand_id,
    brand_name,
    category,
    view_date,
    views,
    round(baseline_avg) as baseline_views,
    round(1.0 * views / nullif(baseline_avg, 0), 2) as lift,
    round((views - baseline_avg) / nullif(baseline_std, 0), 2) as z_score
from scored
where baseline_rows = {{ baseline_days }}
    and (views - baseline_avg) / nullif(baseline_std, 0) >= {{ var('buzz_z_threshold') }}
