-- Share of attention: a brand's slice of all the views its category got that day - the
-- pageview analogue of share of voice. The 7-day averages count rows, which is safe
-- because assert_no_missing_days fails the build on any gap.
with daily as (
    select * from {{ ref('fct_brand_daily') }}
),

shares as (
    select
        brand_id,
        brand_name,
        category,
        view_date,
        views,
        1.0 * views / nullif(sum(views) over (partition by category, view_date), 0)
            as share_of_attention,
        avg(views) over (
            partition by brand_id order by view_date rows between 6 preceding and current row
        ) as views_7d_avg
    from daily
)

select
    *,
    avg(share_of_attention) over (
        partition by brand_id order by view_date rows between 6 preceding and current row
    ) as share_7d_avg,
    views_7d_avg
        / nullif(lag(views_7d_avg, 7) over (partition by brand_id order by view_date), 0)
        - 1 as views_wow_change
from shares
