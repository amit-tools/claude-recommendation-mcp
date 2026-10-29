uv run mcp dev server.py
Browser will open - http://127.0.0.1:6274/?MCP_INSPECTOR_API_TOKEN=c47c48c39e31c7c478b75c49b88393953267e781d659872bb77192fe5c09bd48

connect - Tools - Josn mode
{
  "consumer_id": 100,
  "limit": 10
}


Output

{
  "consumerId": 100,
  "strategy": "COLLABORATIVE_PLUS_POPULARITY",
  "coldStart": false,
  "recommendations": [
    {
      "rank": 1,
      "merchantKey": "PARTNER_MERCHANT_...",
      "merchantName": "...",
      "category": "...",
      "score": 0.91,
      "collaborativeScore": 0.89,
      "popularityScore": 0.97,
      "reason": "Similar to merchants previously used"
    }
  ]
}

