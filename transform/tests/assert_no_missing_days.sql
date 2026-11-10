-- Every brand has every day between its first and last loaded date: the marts' rolling
-- windows count rows, so a missing day would silently stretch "7 days" to 8.
select
    brand_id,
    min(view_date) as first_day,
    max(view_date) as last_day,
    count(*) as days_loaded
from {{ ref('fct_brand_daily') }}
group by brand_id
having count(*) != {{ dbt.datediff('min(view_date)', 'max(view_date)', 'day') }} + 1
