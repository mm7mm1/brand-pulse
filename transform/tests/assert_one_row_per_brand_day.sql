select brand_id, view_date, count(*) as row_count
from {{ ref('fct_brand_daily') }}
group by brand_id, view_date
having count(*) > 1
