select category, view_date, sum(share_of_attention) as total_share
from {{ ref('fct_share_of_attention') }}
group by category, view_date
having abs(sum(share_of_attention) - 1) > 1e-9
