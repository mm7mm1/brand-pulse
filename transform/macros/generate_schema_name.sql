{% macro generate_schema_name(custom_schema_name, node) -%}
    {#- Layers map to fixed schemas (config, silver, gold) in every target, so dbt writes
        straight into the BigQuery datasets Terraform owns instead of `silver_gold`. -#}
    {{ (custom_schema_name or target.schema) | trim }}
{%- endmacro %}
