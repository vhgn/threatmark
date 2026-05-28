Asynchronous Database Access Engineering Acceptance

# System Requirements

Endpoints:
There will be two HTTP public endpoints:

Inference Endpoint: This endpoint accepts a member a from a set A and returns a statistical summary
of a based on data obtained from the second endpoint (ingest endpoint).

Ingest Endpoint: This endpoint accepts a binary relation (a, b) where both a and b are members of
set A . It does not return any response, it merely processes the data.

Inference Endpoint:
For a given set A member a , the inference endpoint returns a numerical vector that encodes the following
statistics:
- The number of times a has been on the right side of an ingest relation.
- The number of times a has been on the right side of an ingest relation in the last 7 days.
- The time since a first appeared on the right side of the ingest relation.
- The time since a last appeared on the right side of the ingest relation.
- The number of distinct items that appeared on the left side of the ingest relation when a was on the right side.

# Data Retention and Storage

All data from incoming ingest payloads must be permanently stored with a retention period of 3 years.
Set Characteristics:
The cardinality of set A is approximately 100 million.
For each member a , the expected distinct count of other members b in ingested relations (a, b) is
approximately 50.

The length in bytes of each member of set A is constant at 16 bytes.

# Key Design Considerations

When designing the system, please address the following aspects:
Data Storage:
- What data storage solutions would you propose for storing data with a 3-year retention period
- What storage mechanisms are best suited for handling high write throughput while also ensuring durability?

# Expected Deliverables
Please submit a document that includes the following:
- A basic Python module that utilizes asyncio and sqlalchemy libraries to communicate with a database in such a manner that could be efficiently applied in forementioned API endpoints.
- A discussion of the appropriate data storage solutions and how they align with the system requirements.

You are encouraged to include diagrams, text descriptions, and code snippets as needed to illustrate your
proposed solution.
This challenge is designed to evaluate both your technical expertise and your ability to think critically about
building high-performance, scalable systems in a cloud environment. We look forward to reviewing your
solution!
