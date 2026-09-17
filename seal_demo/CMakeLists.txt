cmake_minimum_required(VERSION 3.16)

project(medical_he_demo LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(SEAL 4.4 REQUIRED)

add_executable(
    medical_he_demo
    main.cpp
)

add_executable(
    hospital_encrypt
    separated/hospital_encrypt.cpp
)

add_executable(
    researcher_compute
    separated/researcher_compute.cpp
)

add_executable(
    hospital_decrypt
    separated/hospital_decrypt.cpp
)

add_executable(
    researcher_sum
    separated/researcher_sum.cpp
)

add_executable(
    hospital_decrypt_sum
    separated/hospital_decrypt_sum.cpp
)

target_link_libraries(
    medical_he_demo
    PRIVATE
    SEAL::seal
)

target_link_libraries(
    hospital_encrypt
    PRIVATE
    SEAL::seal
)

target_link_libraries(
    researcher_compute
    PRIVATE
    SEAL::seal
)

target_link_libraries(
    hospital_decrypt
    PRIVATE
    SEAL::seal
)

target_link_libraries(
    researcher_sum
    PRIVATE
    SEAL::seal
)

target_link_libraries(
    hospital_decrypt_sum
    PRIVATE
    SEAL::seal
)
