"""
Configuration and constants for the risk management system.
"""

from datetime import datetime


class PredictionConfig:
    """Configuration for prediction runs and database persistence."""

    @staticmethod
    def get_current_academic_year() -> str:
        """
        Get the current academic year based on system date.
        
        Academic year runs from August to July.
        Examples: "2024-2025", "2025-2026"
        """
        now = datetime.now()
        year = now.year
        
        # If before August, academic year started previous year
        if now.month < 8:
            return f"{year - 1}-{year}"
        else:
            return f"{year}-{year + 1}"

    @staticmethod
    def get_current_semester() -> str:
        """
        Get the current semester.
        
        Semester 1: August - December
        Semester 2: January - July
        """
        month = datetime.now().month
        if month >= 8:
            return "1"
        else:
            return "2"

    @staticmethod
    def get_model_version(model_id: str = "rf") -> str:
        """Get model version string for database tracking."""
        return f"{model_id}_v1"


# Configuration constants
RISK_CATEGORIES = {
    "high_risk": "High Risk",
    "moderate_risk": "Moderate Risk",
    "low_risk": "Low Risk",
}

COLLEGES = {
    "CITE": "College of Information Technology and Engineering",
    "CBAA": "College of Business, Accountancy and Administration",
    "CTE": "College of Teacher Education",
    "COED": "College of Education",
    "CON": "College of Nursing",
    "CAS": "College of Arts and Sciences",
}
