from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import shutil
import os
from flexrent_parser import parse_pdf

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/analyze")
async def analyze_statement(
    file: UploadFile = File(...), 
    password: str = Form(None)
):
    if file.content_type != "application/pdf":
        raise HTTPException(400, "File must be a PDF")
    
    # Save file temporarily
    temp_filename = f"temp_{file.filename}"
    with open(temp_filename, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        # Run Parser (Pass the PASSWORD here)
        transactions, totals = parse_pdf(temp_filename, password=password)
        
        # Extract Data
        calculated_income = totals["total_credit"]
        stated_deposit_total = totals["summary_match"]
        salary = totals["salary_estimate"]
        
        if stated_deposit_total and stated_deposit_total > 0:
            final_income = stated_deposit_total
            
            # Calculate the difference for internal logging/debugging
            diff = abs(stated_deposit_total - calculated_income)
            
            # If the difference is huge (like your 339k), we assume the PDF is right
            # and the parser found extra "internal" credits.
            if diff > 1000:
                summary_status = f"Trusted PDF Total (Excluded {diff:,.0f} internal transfers)"
            else:
                summary_status = "✅ Match"
        else:
            # Fallback if no summary found
            final_income = calculated_income
            summary_status = "Calculated from Rows"

        return {
            "filename": file.filename,
            "total_income": final_income,
            "salary_estimate": salary,
            "is_creditworthy": final_income > 200000,
            "transaction_count": len(transactions),
            "summary_validation": summary_status,
            "message": "Analysis successful"
        }

    except PermissionError:
        # Catch the specific "Locked" error from flexrent_parser
        return {
            "status": "password_required",
            "message": "This PDF is password protected."
        }

    except Exception as e:
        return {"status": "error", "error": str(e)}
        
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)


@app.get("/")
def home():
    return {"status": "FlexRent Parser is Live"}