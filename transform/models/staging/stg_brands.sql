select
    brand_id,
    brand_name,
    category,
    wiki_project,
    articles,
    is_active
from {{ ref('brands') }}
