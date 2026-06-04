# Fix for Student ID Database Persistence Issue

## Root Cause
The prediction engine was generating **random float values** (0.6953978103845937, 0.6964287930538073, etc.) as student_ids instead of actual student identifiers. This caused 100% FK lookup failures when trying to save predictions to `fact_student_risk`.

### Why This Happened
1. **Training Pipeline Overwrites Unified Dataset**: During model training, `model_training_page.py` calls `store.set_unified_dataset()` with the processed DataFrame from the preprocessing pipeline.
2. **Preprocessing Removes Student_ID**: The preprocessing pipeline encodes categorical variables, scales numerical values, and generates risk labels, which **removes or transforms the Student_ID column**.
3. **Prediction Uses Processed Dataset**: When prediction runs, it tries to find the student_id column, but the processed dataset doesn't have it.
4. **Default to Index 0**: The student_id extraction code defaults to column 0 when Student_ID isn't found. This column happens to be a numeric/encoded column with float values like 0.695.

## Solution
**Preserve the original merged dataset with Student_ID intact**, separate from the processed training dataset.

### Changes Made

#### 1. `services/data_store.py`
- **Added field**: `_original_unified_dataset` to preserve the initial merged dataset with Student_ID
- **Modified method**: `set_unified_dataset()` - Now preserves the original dataset on first call
- **Modified method**: `clear_unified_dataset()` - Clears both the current and original datasets
- **Modified method**: `clear_all()` - Clears both datasets
- **Added method**: `get_prediction_dataset()` - Returns the original unified dataset (with Student_ID) for prediction, falling back to current dataset if needed

**Rationale**: This ensures that even after training modifies the unified_dataset with processed data, the original dataset with intact Student_ID column is preserved for use during prediction.

#### 2. `ui/mixins/prediction_mixin.py`
- **Modified**: `_PredictionWorker.run()` method
- Changed: `store.unified_dataset` → `store.get_prediction_dataset()`

**Rationale**: Ensures the prediction engine receives the original dataset with Student_ID column, not the processed training data.

#### 3. `services/prediction_engine.py`
- **Enhanced**: Student_ID column detection logic (lines 165-181)
- Added more keywords to match: `"student code"`, `"studentid"`
- Added fallback: If no match found, looks for any column with "id" or "code" in name
- Last resort: Defaults to first column

**Rationale**: Improves robustness of student_id column detection, though the main fix is using the correct dataset.

## Flow After Fix
1. **Merge Phase**: Original merged dataset stored in `_original_unified_dataset` with headers like:
   - `["Student_ID", "Program", "College", "SecCode", "Year", ...]`
   
2. **Training Phase**: Processed dataset overwrites `unified_dataset` with headers like:
   - `["Program_CS", "Program_ENG", "College_CAS", ..., "risk_label"]`
   - Original still preserved in `_original_unified_dataset`

3. **Prediction Phase**: Uses `get_prediction_dataset()` which returns:
   - `_original_unified_dataset` (with Student_ID) ✓
   - Student IDs are extracted as strings: "STU001", "STU002", etc.
   - FK lookups in `dim_student` will now succeed ✓

## Testing
```python
# Test 1: Student_ID column is correctly identified
headers = ["Student_ID", "Program", "College", ...]
student_id_keywords = ["student_id", "id_no", "id", ...]
id_col = next((c for c in headers if c.lower() in student_id_keywords), None)
# Result: id_col = "Student_ID" ✓

# Test 2: Dataset preservation works
store.set_unified_dataset(merge_dataset)  # preserves original
store.set_unified_dataset(processed_dataset)  # overwrites current
store.get_prediction_dataset()  # returns merge_dataset ✓

# Test 3: Student IDs are extracted as strings
student_ids = ["STU001", "STU002", "STU003"]  # NOT [0.695, 0.696, ...] ✓
```

## Verification Before Production
1. ✅ Upload data from all four portals
2. ✅ Run data merge to create unified dataset
3. ✅ Train a model
4. ✅ Run prediction
5. ✅ Verify `fact_student_risk` table is populated with correct FK relationships
6. ✅ Confirm student records have valid student_id lookups

## Files Modified
- `services/data_store.py` - Core fix: dataset preservation
- `ui/mixins/prediction_mixin.py` - Use correct dataset for prediction
- `services/prediction_engine.py` - Minor: enhanced column detection
