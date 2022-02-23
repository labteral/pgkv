#!/bin/bash
cd $(dirname $0)
docker-compose -f docker-compose.yaml down
docker-compose -f docker-compose.yaml up --build -d

