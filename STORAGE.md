# Data Storage Solutions

I went ahead with PostgreSQL, because it is in the stack that I will be using for this role. It is suited enough with correct partitioning; an OLAP database wins at extreme scale.

For 3 year data retention strategy I will use partitioning per-month for `relation_events` and purging 3 year old partitions per month to avoid large vacuums. And for `relation_aggregates` I will partition based on `right_id` hash when needed, as we will not be purging the aggregate table.
That logic is not in the implementation.

A better database for extreme throughput scenarios will be ClickHouse DB (or any OLAP database). They are built for append-only ingestion and aggregation pipelines on extreme scales.
