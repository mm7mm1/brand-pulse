{{
  config(
    materialized='incremental',
    unique_key=['brand_id', 'view_date'],
    incremental_strategy=('merge' if target.type == 'bigquery' else 'delete+insert'),
    partition_by={'field': 'view_date', 'data_type': 'date'},
    cluster_by=['category', 'brand_id'],
    on_schema_change='append_new_columns'
  )
}}

-- The source restates recent days, so an incremental run rebuilds a trailing window
-- instead of only the dates it has not seen. A backfill of older months is a --full-refresh.
with pageviews as (
    select * from {{ ref('stg_wiki__pageviews') }}
    {% if is_incremental() %}
    where view_date >= (
        select cast({{ dbt.dateadd('day', -var('lookback_days'), 'max(view_date)') }} as date)
        from {{ this }}
    )
    {% endif %}
)

select
    p.brand_id,
    b.brand_name,
    b.category,
    p.view_date,
    p.views,
    p._loaded_at
from pageviews as p
inner join {{ ref('stg_brands') }} as b on b.brand_id = p.brand_id
