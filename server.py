import os
from typing import Any

import mysql.connector
from dotenv import load_dotenv
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

load_dotenv()

mcp = MCPServer("Customer Recommendations")


def open_connection():
    try:
        return mysql.connector.connect(
            host=os.environ["DB_HOST"],
            port=int(os.getenv("DB_PORT", "3306")),
            database=os.environ["DB_NAME"],
            user=os.environ["DB_USERNAME"],
            password=os.environ["DB_PASSWORD"],
        )
    except KeyError as error:
        raise ToolError(f"Missing required environment variable: {error}") from error


def popularity_fallback(cursor, limit: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT
            merchant_key,
            merchant_name,
            merchant_category,
            merchant_subcategory,
            popularity_score
        FROM merchant_features
        ORDER BY popularity_score DESC, merchant_key
        LIMIT %s
        """,
        (limit,),
    )

    recommendations = []
    for rank, row in enumerate(cursor.fetchall(), start=1):
        recommendations.append(
            {
                "rank": rank,
                "merchantKey": row["merchant_key"],
                "merchantName": row["merchant_name"],
                "category": row["merchant_category"],
                "subcategory": row["merchant_subcategory"],
                "score": float(row["popularity_score"]),
                "collaborativeScore": 0.0,
                "popularityScore": float(row["popularity_score"]),
                "reason": "Popular with consumers",
            }
        )

    return recommendations


@mcp.tool()
def get_customer_recommendations(
    consumer_id: int,
    limit: int = 10,
) -> dict[str, Any]:
    """
    Return ranked merchant recommendations for a consumer.

    Uses merchant affinity and merchant similarity for existing consumers.
    Uses merchant popularity when the consumer has no transaction history.
    """
    if consumer_id <= 0:
        raise ToolError("consumer_id must be greater than zero")

    limit = max(1, min(limit, 50))
    connection = None

    try:
        connection = open_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT COUNT(*) AS affinity_count
            FROM consumer_merchant_affinity
            WHERE consumer_id = %s
            """,
            (consumer_id,),
        )

        affinity_count = cursor.fetchone()["affinity_count"]

        if affinity_count == 0:
            recommendations = popularity_fallback(cursor, limit)
            return {
                "consumerId": consumer_id,
                "strategy": "POPULARITY_FALLBACK",
                "coldStart": True,
                "recommendations": recommendations,
            }

        cursor.execute(
            """
            WITH top_affinity AS (
                SELECT
                    merchant_key,
                    affinity_score
                FROM consumer_merchant_affinity
                WHERE consumer_id = %s
                ORDER BY affinity_score DESC
                LIMIT 10
            ),
            candidates AS (
                SELECT
                    ms.target_merchant_key,
                    SUM(
                        ta.affinity_score * ms.similarity_score
                    ) AS collaborative_score
                FROM top_affinity ta
                JOIN merchant_similarity ms
                  ON ms.source_merchant_key = ta.merchant_key
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM consumer_merchant_affinity existing
                    WHERE existing.consumer_id = %s
                      AND existing.merchant_key =
                          ms.target_merchant_key
                )
                GROUP BY ms.target_merchant_key
            ),
            scored AS (
                SELECT
                    candidate.*,
                    MAX(candidate.collaborative_score) OVER ()
                        AS maximum_collaborative_score
                FROM candidates candidate
            )
            SELECT
                mf.merchant_key,
                mf.merchant_name,
                mf.merchant_category,
                mf.merchant_subcategory,
                COALESCE(
                    scored.collaborative_score /
                        NULLIF(scored.maximum_collaborative_score, 0),
                    0
                ) AS normalized_collaborative_score,
                mf.popularity_score,
                (
                    0.80 * COALESCE(
                        scored.collaborative_score /
                        NULLIF(scored.maximum_collaborative_score, 0),
                        0
                    )
                    + 0.20 * mf.popularity_score
                ) AS final_score
            FROM scored
            JOIN merchant_features mf
              ON mf.merchant_key = scored.target_merchant_key
            ORDER BY final_score DESC
            LIMIT %s
            """,
            (consumer_id, consumer_id, limit),
        )

        rows = cursor.fetchall()

        if not rows:
            return {
                "consumerId": consumer_id,
                "strategy": "POPULARITY_FALLBACK",
                "coldStart": True,
                "recommendations": popularity_fallback(cursor, limit),
            }

        recommendations = []
        for rank, row in enumerate(rows, start=1):
            recommendations.append(
                {
                    "rank": rank,
                    "merchantKey": row["merchant_key"],
                    "merchantName": row["merchant_name"],
                    "category": row["merchant_category"],
                    "subcategory": row["merchant_subcategory"],
                    "score": round(float(row["final_score"]), 6),
                    "collaborativeScore": round(
                        float(row["normalized_collaborative_score"]), 6
                    ),
                    "popularityScore": round(
                        float(row["popularity_score"]), 6
                    ),
                    "reason": "Similar to merchants previously used",
                }
            )

        return {
            "consumerId": consumer_id,
            "strategy": "COLLABORATIVE_PLUS_POPULARITY",
            "coldStart": False,
            "recommendations": recommendations,
        }

    except mysql.connector.Error as error:
        raise ToolError(f"Recommendation database error: {error}") from error
    finally:
        if connection is not None and connection.is_connected():
            connection.close()


if __name__ == "__main__":
    mcp.run()
