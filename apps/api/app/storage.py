import hashlib
from botocore.config import Config
import boto3
from .config import settings

def client():
    s=settings()
    return boto3.client("s3",endpoint_url=s.s3_endpoint_url,aws_access_key_id=s.s3_access_key,aws_secret_access_key=s.s3_secret_key,region_name=s.s3_region,config=Config(signature_version="s3v4"))
def put(key:str,data:bytes,content_type:str):
    s=settings(); c=client()
    try: c.head_bucket(Bucket=s.s3_bucket)
    except Exception: c.create_bucket(Bucket=s.s3_bucket)
    c.put_object(Bucket=s.s3_bucket,Key=key,Body=data,ContentType=content_type,ServerSideEncryption="AES256")
def get(key:str)->bytes:return client().get_object(Bucket=settings().s3_bucket,Key=key)["Body"].read()
def signed_get(key:str,seconds:int=300)->str:return client().generate_presigned_url("get_object",Params={"Bucket":settings().s3_bucket,"Key":key},ExpiresIn=seconds)
def digest(data:bytes)->str:return hashlib.sha256(data).hexdigest()
