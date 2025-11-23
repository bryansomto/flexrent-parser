from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pdfplumber
from pdfminer.pdfdocument import PDFPasswordIncorrect
import pandas as pd
import io
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1. CHANGED: Now accepts the 'pdf' object, not a filename/stream
def parse_nigerian_statement(pdf_object):
    """
    Scans an already opened PDF object for credit transactions.
    """
    total_credit = 0.0
    
    # Loop through the pages of the EXISTING pdf object
    for page in pdf_object.pages:
        tables = page.extract_tables()
        
        for table in tables:
            df = pd.DataFrame(table)
            
            # Skip empty tables
            if df.empty or len(df) < 2: 
                continue
            
            # Normalize headers to lowercase
            # We convert to string just in case there's a None/NaN in the header
            new_header = df.iloc[0].astype(str).str.lower() 
            df = df[1:] 
            df.columns = new_header

            # 2. LOGIC UPGRADE: Exclude "Balance" columns
            # Many banks have a "Credit Balance" column. If you sum that, 
            # you will get 100x the actual income.
            # We look for "credit" or "cr", BUT NOT "balance".
            credit_col = [c for c in df.columns if ("credit" in c or "cr" in c) and "balance" not in c]
            
            if credit_col:
                target_col = credit_col[0] 
                
                for amount in df[target_col]:
                    if amount:
                        # Clean string: Remove commas, 'N', spaces, etc.
                        clean_amount = re.sub(r'[^\d.]', '', str(amount))
                        try:
                            val = float(clean_amount)
                            total_credit += val
                        except ValueError:
                            continue
                                
    return total_credit

@app.post("/analyze")
async def analyze_statement(
    file: UploadFile = File(...), 
    password: str = Form(None)
):
    if file.content_type != "application/pdf":
        raise HTTPException(400, "File must be a PDF")
    
    content = await file.read()
    file_stream = io.BytesIO(content)
    
    try:
        # Open the PDF ONCE here
        with pdfplumber.open(file_stream, password=password) as pdf:
            
            # 3. FIX: Pass the 'pdf' variable, NOT 'file_stream'
            income = parse_nigerian_statement(pdf)
            
            return {
                "filename": file.filename,
                "total_income": income,
                "is_creditworthy": income > 200000, 
                "message": "Analysis successful"
            }

    except PDFPasswordIncorrect:
        return {
            "status": "password_required",
            "message": "This PDF is password protected."
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/")
def home():
    return {"status": "FlexRent Parser is Live"}