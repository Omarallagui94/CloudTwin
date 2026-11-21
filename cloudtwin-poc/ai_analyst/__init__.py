"""Analyste IA de CloudTwin : explique un rapport d'analyse, sans jamais modifier le risque.

    from ai_analyst import explain
    explanation = explain(analysis_result)   # Explanation(summary, recommendations, source)
"""

from ai_analyst.analyst import explain
from ai_analyst.schema import Explanation, Recommendation

__all__ = ["Explanation", "Recommendation", "explain"]
