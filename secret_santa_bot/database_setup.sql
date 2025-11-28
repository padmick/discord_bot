-- Database setup script for Secret Santa Bot
-- Run this as a database administrator or user with CREATE privileges

-- Create participants table
CREATE TABLE IF NOT EXISTS participants (
    id SERIAL PRIMARY KEY,
    user_id TEXT UNIQUE,
    name TEXT,
    wishlist TEXT,
    address TEXT,
    is_creator BOOLEAN DEFAULT FALSE
);

-- Create pairings table
CREATE TABLE IF NOT EXISTS pairings (
    id SERIAL PRIMARY KEY,
    giver_id TEXT,
    receiver_id TEXT,
    FOREIGN KEY(giver_id) REFERENCES participants(user_id),
    FOREIGN KEY(receiver_id) REFERENCES participants(user_id)
);

-- Grant permissions to the application user (replace 'app_user' with actual username)
-- GRANT SELECT, INSERT, UPDATE, DELETE ON participants TO app_user;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON pairings TO app_user;
-- GRANT USAGE, SELECT ON SEQUENCE participants_id_seq TO app_user;
-- GRANT USAGE, SELECT ON SEQUENCE pairings_id_seq TO app_user;
