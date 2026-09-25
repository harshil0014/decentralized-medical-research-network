cmake_minimum_required(VERSION 3.16)

project(medical_he_demo LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(SEAL 4.4 REQUIRED)

add_executable(medical_he_demo main.cpp)

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

add_executable(
    dicom_hospital_encrypt_stats
    dicom/hospital_encrypt_stats.cpp
)
add_executable(
    dicom_researcher_compute_stats
    dicom/researcher_compute_stats.cpp
)
add_executable(
    dicom_hospital_decrypt_stats
    dicom/hospital_decrypt_stats.cpp
)
add_executable(
    dicom_hospital_encrypt_raw
    dicom/hospital_encrypt_raw.cpp
)
add_executable(
    dicom_researcher_compute_raw
    dicom/researcher_compute_raw.cpp
)

set(SEAL_TARGETS
    medical_he_demo
    hospital_encrypt
    researcher_compute
    hospital_decrypt
    researcher_sum
    hospital_decrypt_sum
    dicom_hospital_encrypt_stats
    dicom_researcher_compute_stats
    dicom_hospital_decrypt_stats
    dicom_hospital_encrypt_raw
    dicom_researcher_compute_raw
)

foreach(target_name IN LISTS SEAL_TARGETS)
    target_link_libraries(
        ${target_name}
        PRIVATE
        SEAL::seal
    )
endforeach()
