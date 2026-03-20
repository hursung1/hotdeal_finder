from database import engine, Base
import models # 모델 클래스들이 Base에 등록되도록 임포트

def init():
    print("데이터베이스 초기화 및 테이블 생성 중...")
    Base.metadata.create_all(bind=engine)
    print("완료: hotdeal.db 파일 및 테이블이 성공적으로 생성되었습니다.")

if __name__ == "__main__":
    init()
