-- Typed, one row per brand and day. The lake holds exactly one file per brand-month, so
-- there is no dedup here; the uniqueness test on fct_brand_daily guards that contract.
select
    brand_id,
    wiki_project,
    articles,
    cast(date as date) as view_date,
    cast(views as {{ dbt.type_bigint() }}) as views,
    _loaded_at
from {{ source('bronze', 'pageviews') }}
