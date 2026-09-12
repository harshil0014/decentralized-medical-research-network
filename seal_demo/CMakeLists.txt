cmake_minimum_required(VERSION 3.16)

project(medical_he_demo LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(SEAL 4.4 REQUIRED)

add_executable(
    medical_he_demo
    main.cpp
)

target_link_libraries(
    medical_he_demo
    PRIVATE
    SEAL::seal
)
