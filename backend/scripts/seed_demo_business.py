from app.db.models import Business
from app.db.session import SessionLocal


def seed_demo_business() -> None:
    session = SessionLocal()
    try:
        existing = (
            session.query(Business)
            .filter(Business.external_id == "demo")
            .first()
        )
        if existing is None:
            existing = session.query(Business).filter(Business.name == "Demo Restaurant").first()
        if existing is not None:
            if existing.external_id != "demo":
                existing.external_id = "demo"
                session.commit()
            print(f"Demo business already exists with id={existing.id}")
            return

        demo = Business(
            external_id="demo",
            name="Demo Restaurant",
            timezone="America/New_York",
            phone="+15555550100",
            transfer_phone="+15555550199",
            hours_json={
                "mon_fri": [{"start": "09:00", "end": "21:00"}],
                "sat_sun": [{"start": "10:00", "end": "22:00"}],
            },
            policies_json={"max_party_size": 8, "booking_window_days": 30},
        )
        session.add(demo)
        session.commit()
        session.refresh(demo)
        print(f"Created demo business with id={demo.id}")
    finally:
        session.close()


if __name__ == "__main__":
    seed_demo_business()
